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
from typing import Any

from fastapi import FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field

from transbook.ingest.epub import slugify
from transbook.service import jobs as J
from transbook.service import pipeline as P
from transbook.store import connect
from transbook.store import db as store

#: SSE 轮询间隔（秒）。作业状态本来就只有百来个检查点，没必要更密。
SSE_INTERVAL = 0.4
#: SSE 最长挂多久（秒），防止连接泄漏；到点会让客户端重连。
SSE_MAX_SECONDS = 3600


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
            return {"detail": "前端未构建：在 web/ 下执行 npm install && npm run build，"
                              "或用 `npm run dev` 起开发服务器"}

    return app


def find_web_dist(start: str | Path | None = None) -> Path | None:
    """定位前端构建产物 `web/dist`。

    从当前目录向上找，兼容"在项目根跑"与"在别处跑"两种情况。
    """
    here = Path(start) if start else Path(__file__).resolve()
    for base in [Path.cwd(), *Path(here).parents]:
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
    return {
        "doc_id": p.doc_id,
        "dir": str(p.dir),
        "has_ir": p.ir_path.is_file(),
        "has_db": p.db_path.is_file(),
        "source": src.name if src else None,
        "outputs": sorted(f.name for f in p.dir.glob("*.epub")) +
                   sorted(f.name for f in p.dir.glob("*.pdf")),
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
