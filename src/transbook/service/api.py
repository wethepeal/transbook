"""HTTP 服务（M5）：提交一本书 → 查进度（含 SSE）→ 交审核。

设计取舍：
* **作业状态在 SQLite，不在内存**：服务重启不丢进度，SSE 只是轮询这张表，
  不需要额外引入消息队列或 Redis。
* **每个请求各开一次连接**：FastAPI 的同步端点跑在线程池里，SQLite 连接不跨线程复用。
* 接口只做"参数校验 + 转调 `service.pipeline`"，业务逻辑全在流水线层，
  CLI 与 HTTP 共享同一套实现。
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path
from typing import Any, NamedTuple

from fastapi import FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field

from transbook import config as cfg
from transbook.ingest.epub import slugify
from transbook.service import jobs as J
from transbook.service import pipeline as P
from transbook.store import connect
from transbook.store import db as store

#: SSE 轮询间隔（秒）。作业状态本来就只有百来个检查点，没必要更密。
SSE_INTERVAL = 0.4
#: SSE 最长挂多久（秒），防止连接泄漏；到点会让客户端重连。
SSE_MAX_SECONDS = 3600

class ConfigFieldSpec(NamedTuple):
    """配置页的一项：键名、界面标签、是否机密、说明、可选值。"""

    name: str
    label: str
    secret: bool
    hint: str
    #: 非空时界面渲染成下拉框；空值项表示"用默认"
    options: tuple[tuple[str, str], ...] = ()


#: 「默认模型」的下拉选项，取值来自 DeepSeek 官方定价页（Models & Pricing）。
#: 目前在售的**只有** deepseek-flash 与 deepseek-v4-pro 两个；老的
#: `deepseek-chat` / `deepseek-reasoner` 已不在文档里，不该再作为推荐项出现。
#: 空值表示用引擎默认——`DeepSeekProvider` 的默认模型就是 `deepseek-flash`。
MODEL_CHOICES: tuple[tuple[str, str], ...] = (
    ("", "默认（deepseek-flash）"),
    ("deepseek-flash", "deepseek-flash — 便宜快，支持图片输入"),
    ("deepseek-v4-pro", "deepseek-v4-pro — 能力更强，单价约为 flash 的 3 倍"),
)

#: 配置页可编辑的项。机密项**永远不会把明文回传给前端**，只回显打码后的首尾。
CONFIG_FIELDS: tuple[ConfigFieldSpec, ...] = (
    ConfigFieldSpec("DEEPSEEK_API_KEY", "DeepSeek API Key", True,
                    "翻译必需的密钥。去 platform.deepseek.com 申请；换机器/重新部署后要重新填。"),
    ConfigFieldSpec("DEEPSEEK_BASE_URL", "接口地址", False,
                    "留空用 DeepSeek 官方。填本地地址（如 http://127.0.0.1:8117/v1）即可改用本地模型。"),
    ConfigFieldSpec("TRANSLATE_MODEL", "默认模型", False,
                    "留空即用引擎默认。接了本地端点时选「自定义」填任意模型名。",
                    options=MODEL_CHOICES),
)


class JobRequest(BaseModel):
    """提交一个作业。`params` 原样透传给流水线。"""

    kind: str = Field(description="full / extract / import / translate / summarize / render")
    params: dict[str, Any] = Field(default_factory=dict)


class ReviewRequest(BaseModel):
    """回灌校对结果（TSV 全文）。"""

    tsv: str


class SegmentPatch(BaseModel):
    """单段保存（校对界面逐段编辑用）。"""

    final_translation: str | None = None
    #: 传 true 可把该段的定稿清空，回到机翻
    clear: bool = False


class ConfigPatch(BaseModel):
    """更新 `.env` 配置。

    只提交**要改的键**；值为空串表示清空该项。刻意不做"整表覆盖"：
    那样前端每次都得把密钥原样回传，明文来回多绕一圈没有意义。
    """

    values: dict[str, str] = Field(default_factory=dict)


def mask_secret(value: str) -> str:
    """密钥只回显首尾——前端要能显示"已配置"，但不能拿到明文。"""
    if not value:
        return ""
    if len(value) <= 10:
        return "*" * len(value)
    return f"{value[:6]}{'*' * 6}{value[-4:]}"


def create_app(root: str | Path = P.DEFAULT_ROOT,
               db_path: str | Path | None = None,
               web: str | Path | None = None) -> FastAPI:
    """构造应用。`root` 下每个子目录是一个项目；`web` 指向前端构建产物目录。"""
    from contextlib import asynccontextmanager

    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    service_db = Path(db_path) if db_path else root / "service.db"
    web_dir = Path(web) if web else find_web_dist()

    def conn():
        return connect(service_db)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        c = conn()
        try:
            n = J.reap_stale(c)  # 上次没退干净的 running 作业收尸
            if n:
                print(f"[transbook] 收尸 {n} 个中断的作业")
        finally:
            c.close()
        yield

    app = FastAPI(
        title="transbook service",
        version="0.1.0",
        description="电子书翻译流水线的 HTTP 接口（M5）",
        lifespan=lifespan,
    )

    def project_or_404(doc_id: str) -> P.Project:
        proj = P.project_of(root, doc_id)
        if not proj.exists():
            raise HTTPException(404, f"项目不存在：{doc_id}")
        return proj

    # ── 健康检查与项目 ──────────────────────────────────────────────
    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"status": "ok", "root": str(root), "db": str(service_db)}

    # ── 配置（部署后换机器 / 重新填密钥用）──────────────────────────
    @app.get("/api/config")
    def get_config() -> dict[str, Any]:
        """当前生效的配置。**机密项只回打码值**，绝不回明文。"""
        path = cfg.env_write_path()
        fields = []
        for spec in CONFIG_FIELDS:
            raw = (cfg.get(spec.name) or "").strip()
            fields.append({
                "name": spec.name,
                "label": spec.label,
                "hint": spec.hint,
                "secret": spec.secret,
                "is_set": bool(raw),
                # 机密项连"值"都不给，前端只能拿到打码串
                "value": "" if spec.secret else raw,
                "masked": mask_secret(raw) if spec.secret else "",
                # 可选值由后端下发，避免模型清单在前后端各维护一份、迟早对不上
                "options": [{"value": v, "label": lb} for v, lb in spec.options],
            })
        return {
            "env_file": str(path),
            "env_file_exists": path.is_file(),
            "fields": fields,
            "active": {
                "engine": (cfg.get("TRANSLATE_ENGINE") or "deepseek").strip(),
                "model": (cfg.get("TRANSLATE_MODEL") or "").strip() or "（引擎默认）",
                "key_set": bool((cfg.get("DEEPSEEK_API_KEY") or "").strip()),
            },
        }

    @app.put("/api/config")
    def put_config(patch: ConfigPatch) -> dict[str, Any]:
        """写进 `.env` 并让**当前进程立刻生效**，不必重启服务。

        作业在独立子进程里跑，它继承本进程的工作目录与环境变量，
        所以改完立刻提交的作业也会用上新配置。
        """
        known = {spec.name for spec in CONFIG_FIELDS}
        unknown = sorted(set(patch.values) - known)
        if unknown:
            raise HTTPException(400, f"不认识的配置项：{', '.join(unknown)}")
        cleaned = {k: (v or "").strip() for k, v in patch.values.items()}
        if not cleaned:
            return {"ok": True, "changed": [], "env_file": str(cfg.env_write_path())}
        path = cfg.apply_env_values(cleaned)
        return {"ok": True, "changed": sorted(cleaned), "env_file": str(path)}

    @app.get("/api/projects")
    def list_projects() -> list[dict[str, Any]]:
        return [_project_view(p) for p in P.list_projects(root)]

    @app.post("/api/books", status_code=201)
    async def upload_book(file: UploadFile,
                          doc_id: str | None = Form(None),
                          engine: str = Form("deepseek"),
                          mode: str = Form("bilingual"),
                          to: str = Form("epub"),
                          translate: bool = Form(True)) -> dict[str, Any]:
        """上传一本书并（默认）直接开始整条流水线。"""
        data = await file.read()
        if not data:
            raise HTTPException(400, "空文件")
        did = doc_id or slugify(Path(file.filename or "book").stem)
        try:
            P.store_source(root, did, file.filename or "book.epub", data)
        except P.PipelineError as exc:
            raise HTTPException(400, str(exc)) from exc
        c = conn()
        try:
            if translate:
                params = {"engine": engine, "mode": mode, "to": to}
                jid = J.submit(c, kind="full", doc_id=did, params=params,
                               db_path=service_db, root=root)
            else:
                jid = J.submit(c, kind="extract", doc_id=did, params={},
                               db_path=service_db, root=root)
        finally:
            c.close()
        return {"doc_id": did, "job_id": jid}

    @app.get("/api/books/{doc_id}")
    def book_detail(doc_id: str) -> dict[str, Any]:
        proj = project_or_404(doc_id)
        view = _project_view(proj)
        if proj.ir_path.is_file():
            ir = P.load_ir(proj)
            view["ir"] = {
                "title": ir.doc.title, "author": ir.doc.author,
                "source_lang": ir.doc.source_lang, "origin": ir.doc.origin,
                "vertical": ir.doc.vertical, "blocks": len(ir.blocks),
                "counts": ir.counts(), "matter": ir.matter_counts(),
                "toc": len(ir.toc), "translatable": len(ir.translatable()),
                "cover_image": ir.cover_image(),
            }
        if proj.db_path.is_file():
            view["stats"] = _db_stats(proj)
        return view

    # ── 段落与审核 ──────────────────────────────────────────────────
    @app.get("/api/books/{doc_id}/segments")
    def segments(doc_id: str, status: str | None = None, q: str | None = None,
                 offset: int = 0, limit: int = 200) -> dict[str, Any]:
        proj = project_or_404(doc_id)
        if not proj.db_path.is_file():
            raise HTTPException(409, "尚未入库（先跑 import 作业）")
        limit = max(1, min(limit, 1000))
        sql = ("SELECT seg_id, block_id, ord, kind, source_text, translation, "
               "final_translation, status FROM segment")
        where, args = [], []
        if status:
            where.append("status=?")
            args.append(status)
        if q:
            # **三列都要搜**：漏掉 final_translation 的话，人在界面里改完就再也搜不到
            # 自己写的那句（实测踩过）
            where.append("(source_text LIKE ? OR IFNULL(translation,'') LIKE ? "
                         "OR IFNULL(final_translation,'') LIKE ?)")
            args += [f"%{q}%"] * 3
        if where:
            sql += " WHERE " + " AND ".join(where)
        c = connect(proj.db_path)
        try:
            total = c.execute(
                f"SELECT COUNT(*) n FROM ({sql})", args).fetchone()["n"]
            rows = c.execute(f"{sql} ORDER BY ord LIMIT ? OFFSET ?",
                             [*args, limit, offset]).fetchall()
        finally:
            c.close()
        return {"total": total, "offset": offset, "limit": limit,
                "items": [dict(r) for r in rows]}

    @app.patch("/api/books/{doc_id}/segments/{seg_id}")
    def patch_segment(doc_id: str, seg_id: str, body: SegmentPatch) -> dict[str, Any]:
        """保存**单段**定稿——校对界面逐段编辑用。

        与 `/review`（整份 TSV 回灌）互补：TSV 适合离线批量改，
        这个接口适合界面里改一段存一段（不必为了改一个字重传 3400 行）。
        """
        proj = project_or_404(doc_id)
        if not proj.db_path.is_file():
            raise HTTPException(409, "尚未入库")
        c = connect(proj.db_path)
        try:
            row = c.execute("SELECT seg_id, translation FROM segment WHERE seg_id=?",
                            (seg_id,)).fetchone()
            if row is None:
                raise HTTPException(404, f"段落不存在：{seg_id}")
            if body.clear:
                c.execute("UPDATE segment SET final_translation=NULL, updated_at=? "
                          "WHERE seg_id=?", (store._now(), seg_id))
            else:
                if body.final_translation is None:
                    raise HTTPException(400, "需要 final_translation 或 clear=true")
                c.execute("UPDATE segment SET final_translation=?, updated_at=? "
                          "WHERE seg_id=?",
                          (body.final_translation, store._now(), seg_id))
            c.commit()
        finally:
            c.close()
        return {"seg_id": seg_id, "final_translation": None if body.clear
                else body.final_translation}

    @app.get("/api/books/{doc_id}/qa")
    def qa(doc_id: str) -> dict[str, Any]:
        from transbook.quality import check

        proj = project_or_404(doc_id)
        if not proj.db_path.is_file():
            raise HTTPException(409, "尚未入库")
        ir = P.load_ir(proj) if proj.ir_path.is_file() else None
        c = connect(proj.db_path)
        try:
            rep = check(c, ir)
        finally:
            c.close()
        return {"summary": rep.summary(), "counts": rep.counts(),
                "issues": [{"kind": i.kind, "seg_id": i.seg_id, "detail": i.detail,
                            "severity": i.severity}
                           for i in rep.issues[:500]]}

    @app.api_route("/api/books/{doc_id}/files/{name}", methods=["GET", "HEAD"])
    def download_output(doc_id: str, name: str):
        """下载产物（EPUB/PDF）。

        只允许项目目录下的**直接文件**：挡掉 `../` 与子目录，避免目录穿越。
        每个项目一个目录，所以这个限制不影响正常使用。

        同时接受 HEAD：只用 GET 时 HEAD 会返回 405，而链接检查器、部分下载工具
        与断点续传客户端都会先发 HEAD。
        """
        from fastapi.responses import FileResponse

        proj = project_or_404(doc_id)
        if not name or name.startswith(".") or "/" in name or "\\" in name:
            raise HTTPException(400, f"非法文件名：{name}")
        f = (proj.dir / name).resolve()
        if f.parent != proj.dir.resolve() or not f.is_file():
            raise HTTPException(404, f"文件不存在：{name}")
        return FileResponse(f, filename=name)

    @app.get("/api/books/{doc_id}/review.tsv", response_class=PlainTextResponse)
    def export_review(doc_id: str, only_translated: bool = False) -> str:
        from transbook.review import export_tsv

        proj = project_or_404(doc_id)
        if not proj.db_path.is_file():
            raise HTTPException(409, "尚未入库")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "review.tsv"
            c = connect(proj.db_path)
            try:
                export_tsv(c, path, doc_id=doc_id, only_translated=only_translated)
            finally:
                c.close()
            return path.read_text(encoding="utf-8")

    @app.post("/api/books/{doc_id}/review")
    def apply_review(doc_id: str, body: ReviewRequest) -> dict[str, Any]:
        from transbook.review import apply_tsv

        proj = project_or_404(doc_id)
        if not proj.db_path.is_file():
            raise HTTPException(409, "尚未入库")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "review.tsv"
            path.write_text(body.tsv, encoding="utf-8")
            c = connect(proj.db_path)
            try:
                # `apply_tsv` 按 seg_id 回灌，项目库是单文档，不需要再传 doc_id
                st = apply_tsv(c, path)
            finally:
                c.close()
        # 用 asdict 而不是手写字段：ApplyStats 的字段名改过一次，写死会脆
        import dataclasses

        out = dataclasses.asdict(st) if dataclasses.is_dataclass(st) else {"stats": str(st)}
        out["summary"] = st.summary() if hasattr(st, "summary") else str(st)
        return out

    # ── 作业 ────────────────────────────────────────────────────────
    @app.post("/api/books/{doc_id}/jobs", status_code=201)
    def submit_job(doc_id: str, req: JobRequest) -> dict[str, Any]:
        proj = project_or_404(doc_id)
        c = conn()
        try:
            jid = J.submit(c, kind=req.kind, doc_id=proj.doc_id, params=req.params,
                           db_path=service_db, root=root)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        finally:
            c.close()
        return {"job_id": jid, "kind": req.kind}

    @app.get("/api/jobs")
    def list_jobs(doc_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        c = conn()
        try:
            return store.list_jobs(c, limit=max(1, min(limit, 500)), doc_id=doc_id)
        finally:
            c.close()

    @app.get("/api/jobs/{job_id}")
    def job_detail(job_id: str) -> dict[str, Any]:
        c = conn()
        try:
            job = store.get_job(c, job_id)
        finally:
            c.close()
        if job is None:
            raise HTTPException(404, f"作业不存在：{job_id}")
        return job

    @app.delete("/api/jobs/{job_id}")
    def cancel_job(job_id: str) -> dict[str, Any]:
        c = conn()
        try:
            ok = J.cancel(c, job_id)
        finally:
            c.close()
        if not ok:
            raise HTTPException(409, "作业不存在或已结束")
        return {"job_id": job_id, "status": "cancelled"}

    @app.get("/api/jobs/{job_id}/events")
    async def job_events(job_id: str, request: Request,
                         interval: float = SSE_INTERVAL) -> StreamingResponse:
        """SSE 进度流：作业状态一变就推一条，终态后发 `event: end` 并关闭。"""
        async def gen():
            last: str | None = None
            elapsed = 0.0
            step = max(0.05, min(interval, 5.0))
            while elapsed < SSE_MAX_SECONDS:
                if await request.is_disconnected():
                    return
                c = conn()
                try:
                    job = store.get_job(c, job_id)
                finally:
                    c.close()
                if job is None:
                    yield _sse({"error": "not found", "job_id": job_id})
                    return
                snap = json.dumps(_job_view(job), ensure_ascii=False, sort_keys=True)
                if snap != last:
                    last = snap
                    yield _sse(json.loads(snap))
                if job["status"] in store.TERMINAL_STATUS:
                    yield 'event: end\ndata: {"ended": true}\n\n'
                    return
                await asyncio.sleep(step)
                elapsed += step
            yield 'event: end\ndata: {"ended": true, "reason": "timeout"}\n\n'

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache",
                                          "X-Accel-Buffering": "no"})

    # ── 前端（M6）────────────────────────────────────────────────────
    # 注册在**所有 /api 路由之后**：FastAPI 按注册顺序匹配，
    # 兜底路由放前面会把接口全吃掉。
    if web_dir and (web_dir / "index.html").is_file():
        from fastapi.responses import FileResponse
        from fastapi.staticfiles import StaticFiles

        assets = web_dir / "assets"
        if assets.is_dir():
            app.mount("/assets", StaticFiles(directory=assets), name="assets")

        @app.get("/", include_in_schema=False)
        def index() -> Any:
            return FileResponse(web_dir / "index.html")

        @app.get("/{full_path:path}", include_in_schema=False)
        def spa(full_path: str) -> Any:
            # 未命中的 /api 请求必须还是 404，不能回退成 index.html——
            # 否则前端把 HTML 当 JSON 解析，报出莫名其妙的错
            if full_path.startswith("api/"):
                raise HTTPException(404, "接口不存在")
            candidate = (web_dir / full_path).resolve()
            if full_path and candidate.is_file() and web_dir.resolve() in candidate.parents:
                return FileResponse(candidate)
            return FileResponse(web_dir / "index.html")

        app.state.web_dir = web_dir
    else:
        app.state.web_dir = None

        @app.get("/", include_in_schema=False)
        def no_ui() -> Any:
            return {"detail": NO_WEB_HINT}

    return app


#: 找不到前端产物时的提示。源码运行和安装运行都会走到这里，
#: 所以两种补救办法都要说清楚，别让人误以为只能自己去装 Node。
NO_WEB_HINT = (
    "前端未构建。源码运行时在 web/ 下执行 `npm install && npm run build`；"
    "用 uv/pip 安装的**正式发布包自带界面**，若缺失说明装的是未内嵌前端的开发版"
)


def find_web_dist(start: str | Path | None = None) -> Path | None:
    """定位前端构建产物 `web/dist`（含内嵌在 wheel 里的那一份）。

    从两个方向逐级向上找同名目录，两件事其实是一件事：

    1. **源码树**：从当前工作目录向上找 `web/dist`——覆盖"在项目根跑"与
       "在 web/ 之外的子目录跑"两种情况。
    2. **包内嵌**：正式发布包由 `hatch_build.py` 把 `web/dist` 嵌成
       `transbook/web/dist`，装好后位于 `site-packages/transbook/web/dist`；
       该路径恰好落在本文件所在目录的向上查找链上，所以无需额外配置。
    """
    here = Path(start).resolve() if start else Path(__file__).resolve()
    roots: list[Path] = [Path.cwd()]
    roots += [here] if here.is_dir() else []
    roots += list(here.parents)
    for base in roots:
        cand = base / "web" / "dist"
        if (cand / "index.html").is_file():
            return cand
    return None


# ── 小工具 ──────────────────────────────────────────────────────────
def _sse(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _job_view(job: dict[str, Any]) -> dict[str, Any]:
    """SSE 只推会变的那几个字段，避免把 params/result 反复推给浏览器。"""
    return {k: job.get(k) for k in
            ("id", "kind", "doc_id", "status", "stage", "progress", "message",
             "error", "result", "created_at", "started_at", "finished_at")}


def _project_view(p: P.Project) -> dict[str, Any]:
    src = p.source_file()
    # 输入文件也是 .epub / .pdf，按后缀扫会把它当成产物列出来（实测见到了
    # "source.epub" 混在下载清单里）。产物一律排除 source.*。
    outs = sorted(f.name for f in p.dir.iterdir()
                  if f.is_file()
                  and f.suffix.lower() in (".epub", ".pdf")
                  and not f.name.startswith("source."))
    return {
        "doc_id": p.doc_id,
        "dir": str(p.dir),
        "has_ir": p.ir_path.is_file(),
        "has_db": p.db_path.is_file(),
        "source": src.name if src else None,
        "outputs": outs,
    }


def _db_stats(p: P.Project) -> dict[str, Any]:
    c = connect(p.db_path)
    try:
        from transbook.store import stats

        s = stats(c)
        return {"segments": s["n"], "done": s["done"], "cost": s["cost"],
                "by_status": s["by_status"], "tm_entries": s["tm_entries"]}
    finally:
        c.close()
