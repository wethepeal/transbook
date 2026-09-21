"""流水线阶段（M5）——服务层与 CLI 共用的唯一实现。

为什么要单独抽出来：M5 之后有**两个入口**（命令行与 HTTP）。如果各写一套
"抽取→入库→翻译→渲染"，两边一定会漂移（一边修了 bug 另一边没修）。
这里只放"做事"的逻辑，**不打印任何东西**——进度通过 `progress` 回调传出，
由 CLI 转成富文本、由服务转成作业状态。
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from transbook.ingest import EpubError, PdfError, ingestor_for
from transbook.ingest.preview import summarize, to_markdown
from transbook.ir import DocumentIR
from transbook.render.naming import output_stem
from transbook.store import connect, import_ir
from transbook.translate import BookContext, DeepSeekProvider, FakeProvider
from transbook.translate.deepseek import DEFAULT_BASE_URL
from transbook.translate.runner import RunReport, run
from transbook.translate.summary import SummaryReport, generate_summaries

Progress = Callable[[str, float], None]
#: 支持的输入扩展名
SOURCE_EXT = (".epub", ".pdf")
DEFAULT_ROOT = Path("data/work")


class PipelineError(RuntimeError):
    """阶段执行失败（服务层会把它翻译成 4xx/5xx）。"""


# ── 项目路径 ────────────────────────────────────────────────────────
@dataclass
class Project:
    """一个项目 = 工作根目录下的一个文件夹。"""

    doc_id: str
    dir: Path

    @property
    def ir_path(self) -> Path:
        return self.dir / "book.ir.json"

    @property
    def db_path(self) -> Path:
        return self.dir / "translations.db"

    @property
    def assets(self) -> Path:
        return self.dir / "assets"

    def source_file(self) -> Path | None:
        """原始输入文件（上传时按 `source.<ext>` 存下）。"""
        for ext in SOURCE_EXT:
            p = self.dir / f"source{ext}"
            if p.is_file():
                return p
        return None

    def last_activity(self) -> float:
        """最后一次活动时间，用于列表排序。

        取目录里**所有文件**的最新 mtime，而不是目录自身的：目录的 mtime 只在增删
        直接子项时更新，改 `translations.db` 的内容并不会动它——那样翻译完一本书，
        这个项目也不会往前排。
        """
        newest = 0.0
        try:
            for f in self.dir.iterdir():
                if f.is_file():
                    newest = max(newest, f.stat().st_mtime)
        except OSError:
            pass
        if newest:
            return newest
        try:
            return self.dir.stat().st_mtime
        except OSError:
            return 0.0

    def exists(self) -> bool:
        """项目是否存在。

        **不看抽取有没有跑完**：上传只写了 `source.epub`，抽取是后台作业；
        如果这里要求已有 IR/db，用户刚建完项目点进去就会看到"项目不存在"——
        这正是实测遇到的弹窗。存在性只看目录里有没有这个项目的文件。
        """
        return self.ir_path.is_file() or self.db_path.is_file() or self.source_file() is not None


def project_of(root: Path, doc_id: str) -> Project:
    return Project(doc_id=doc_id, dir=Path(root) / doc_id)


def list_projects(root: Path) -> list[Project]:
    """列出工作根下的所有项目，**最近活动的排在前面**。

    两个决定：

    * 用 `exists()` 而不是"有没有 book.ir.json"：刚上传、抽取还没跑完的项目
      也应该出现在列表里，否则用户上传完在首页看不到自己刚建的项目。
    * 按 `last_activity()` 倒序而不是按名字：浏览逻辑是"我刚动过的在最上面"，
      按字母排会让新建的项目沉到列表底部、每次都要找。同一时间戳的按名字排，
      保证顺序稳定、不会每次刷新都跳。
    """
    base = Path(root)
    if not base.is_dir():
        return []
    projects = []
    for d in base.iterdir():
        if not d.is_dir():
            continue
        proj = Project(d.name, d)
        if proj.exists():
            projects.append(proj)
    projects.sort(key=lambda p: (-p.last_activity(), p.doc_id))
    return projects


def store_source(root: Path, doc_id: str, filename: str, data: bytes) -> Project:
    """把上传的文件落到项目目录，返回项目。"""
    ext = Path(filename).suffix.lower()
    if ext not in SOURCE_EXT:
        raise PipelineError(f"不支持的格式 {ext}（支持 {', '.join(SOURCE_EXT)}）")
    proj = project_of(root, doc_id)
    proj.dir.mkdir(parents=True, exist_ok=True)
    (proj.dir / f"source{ext}").write_bytes(data)
    return proj


# ── 各阶段 ──────────────────────────────────────────────────────────
@dataclass
class ExtractResult:
    ir: DocumentIR
    summary: str = ""
    vertical: bool = False
    headers_dropped: int = 0
    ruby_stripped: int = 0


def run_extract(proj: Project, source: Path, *, doc_id: str | None = None,
                assets: bool = True, filter_headers: bool = True,
                strip_ruby: bool = True, limit: int | None = None) -> ExtractResult:
    """① 抽取 → book.ir.json + preview.md + assets/。"""
    if not source.is_file():
        raise PipelineError(f"文件不存在：{source}")
    proj.dir.mkdir(parents=True, exist_ok=True)
    try:
        ing = ingestor_for(source, doc_id=doc_id or proj.doc_id,
                           filter_headers=filter_headers, strip_ruby=strip_ruby)
        ir = ing.extract(assets_dir=proj.assets if assets else None)
    except (EpubError, PdfError, ValueError) as exc:
        raise PipelineError(f"抽取失败：{exc}") from exc
    proj.ir_path.write_text(ir.model_dump_json(indent=2), encoding="utf-8")
    (proj.dir / "preview.md").write_text(to_markdown(ir, limit=limit), encoding="utf-8")
    stats = getattr(ing, "stats", None)
    return ExtractResult(ir=ir, summary=summarize(ir), vertical=bool(ir.doc.vertical),
                         headers_dropped=int(getattr(stats, "headers_dropped", 0) or 0),
                         ruby_stripped=int(getattr(stats, "ruby_stripped", 0) or 0))


def run_import(proj: Project, *, target_lang: str = "zh"):
    """⑤ IR → SQLite 段落表（含 TM 复用与旧译文接续）。"""
    if not proj.ir_path.is_file():
        raise PipelineError(f"缺少 {proj.ir_path}（先抽取）")
    ir = load_ir(proj)
    conn = connect(proj.db_path)
    try:
        return import_ir(conn, ir, target_lang=target_lang)
    finally:
        conn.close()


def load_ir(proj: Project) -> DocumentIR:
    if not proj.ir_path.is_file():
        raise PipelineError(f"缺少 {proj.ir_path}")
    return DocumentIR.model_validate(json.loads(proj.ir_path.read_text(encoding="utf-8")))


def build_provider(engine: str = "deepseek", *, model: str | None = None,
                   base_url: str | None = None, price_tier: str = "peak",
                   api_key: str | None = None):
    """构造翻译引擎。本地端点同样走 OpenAI 兼容协议，但**不计费**。"""
    from transbook.config import get

    if engine == "fake":
        return FakeProvider()
    if engine not in ("deepseek", "local"):
        raise PipelineError(f"未知引擎：{engine}（可选 fake / deepseek / local）")
    key = api_key if api_key is not None else (get("DEEPSEEK_API_KEY") or "")
    if not key:
        raise PipelineError("未配置 DEEPSEEK_API_KEY")
    default_model = get("TRANSLATE_MODEL") or ("deepseek-flash" if engine == "deepseek" else "")
    extra = ({"chat_template_kwargs": {"enable_thinking": False}} if engine == "local"
             else {"thinking": {"type": "disabled"}})
    return DeepSeekProvider(
        key, model=model or default_model or "deepseek-flash",
        base_url=base_url or get("DEEPSEEK_BASE_URL") or DEFAULT_BASE_URL,
        price_tier="local" if engine == "local" else price_tier,
        extra_body=extra,
    )


def load_glossary(path: str | Path | None) -> dict[str, str]:
    from transbook.quality import load_glossary as _load

    return _load(path)


def _band(progress: Progress | None, lo: float, hi: float) -> Callable[[str, float], None] | None:
    """把子阶段的 0~1 进度映射到父区间 [lo, hi]。

    不做映射的话，嵌套阶段会把总进度往回拉（实测 2% → 0% → 92%）。
    结果**夹紧在区间内**：`lo + (hi-lo)*1.0` 会有浮点误差（0.9200000000000002），
    越界一点点就足以破坏"进度单调不减"的断言。
    """
    if progress is None:
        return None

    def cb(msg: str, frac: float) -> None:
        f = max(0.0, min(1.0, frac))
        progress(msg, min(hi, max(lo, lo + (hi - lo) * f)))

    return cb


def run_translate(proj: Project, *, engine: str = "deepseek", model: str | None = None,
                  base_url: str | None = None, price_tier: str = "peak",
                  glossary: str | Path | None = None, limit: int | None = None,
                  max_cost: float = 0.0, batch_chars: int = 2400, batch_items: int = 24,
                  target_lang: str = "zh", rolling_summary: bool = False,
                  dry_run: bool = False, progress: Progress | None = None,
                  band: tuple[float, float] = (0.0, 1.0)) -> RunReport:
    """③ 翻译（可断点续跑、有成本护栏）。"""
    if not proj.db_path.is_file():
        raise PipelineError(f"缺少 {proj.db_path}（先入库）")
    provider = build_provider(engine, model=model, base_url=base_url,
                              price_tier=price_tier)
    conn = connect(proj.db_path)
    try:
        doc = conn.execute("SELECT * FROM doc LIMIT 1").fetchone()
        ctx = BookContext(
            doc_id=doc["id"] if doc else proj.doc_id,
            title=doc["title"] if doc else "",
            author=doc["author"] if doc else "",
            source_lang=(doc["source_lang"] if doc else "") or "ja",
            target_lang=target_lang, glossary=load_glossary(glossary),
        )
        ir = load_ir(proj) if rolling_summary else None
        return run(conn, provider, ctx, limit=limit, max_cost=max_cost,
                   batch_chars=batch_chars, batch_items=batch_items, dry_run=dry_run,
                   ir=ir, rolling_summary=rolling_summary,
                   progress=_band(progress, *band))
    finally:
        conn.close()


def run_summarize(proj: Project, *, engine: str = "deepseek", model: str | None = None,
                  base_url: str | None = None, price_tier: str = "peak",
                  budget: int = 800, window: int = 4, chapter_chars: int = 6000,
                  force: bool = False, progress: Progress | None = None) -> SummaryReport:
    """⑩ 按章生成短摘要（供 `--rolling-summary` 注入）。"""
    ir = load_ir(proj)
    if not proj.db_path.is_file():
        raise PipelineError(f"缺少 {proj.db_path}（先入库）")
    provider = build_provider(engine, model=model, base_url=base_url,
                              price_tier=price_tier)
    ctx = BookContext(doc_id=ir.doc.id, title=ir.doc.title, author=ir.doc.author,
                      source_lang=ir.doc.source_lang or "ja")
    conn = connect(proj.db_path)
    try:
        return generate_summaries(conn, provider, ir, ctx, budget=budget, window=window,
                                  chapter_chars=chapter_chars, force=force,
                                  progress=_band(progress, 0.05, 1.0))
    finally:
        conn.close()


@dataclass
class RenderResult:
    files: list[Path] = field(default_factory=list)
    chapters: int = 0
    covered: int = 0
    translatable: int = 0
    error: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"files": [str(p) for p in self.files], "chapters": self.chapters,
                "covered": self.covered, "translatable": self.translatable,
                "error": self.error}


def run_render(proj: Project, *, mode: str = "bilingual", to: str = "epub",
               out_dir: Path | None = None) -> RenderResult:
    """⑥ IR + 译文 → EPUB / PDF。与 CLI 走同一套渲染器。"""
    from transbook.render import build_chapters, build_nav, render_pdf, write_epub
    from transbook.render.xhtml import CSS

    ir = load_ir(proj)
    if not proj.db_path.is_file():
        raise PipelineError(f"缺少 {proj.db_path}（先入库）")
    conn = connect(proj.db_path)
    try:
        rows = conn.execute(
            "SELECT seg_id, COALESCE(final_translation, translation) AS t "
            "FROM segment WHERE COALESCE(final_translation, translation) IS NOT NULL"
        ).fetchall()
    finally:
        conn.close()
    translations = {r["seg_id"].split(":", 1)[1]: r["t"] for r in rows}

    dest = Path(out_dir) if out_dir else proj.dir
    dest.mkdir(parents=True, exist_ok=True)
    wants = {"epub", "pdf"} if to == "both" else {to}
    res = RenderResult()
    res.chapters = sum(1 for b in ir.blocks if b.type == "heading")
    res.translatable = len(ir.translatable())
    res.covered = sum(1 for b in ir.translatable()
                      if all(translations.get(u) for u in b.unit_ids()))

    if "epub" in wants:
        chapters = build_chapters(ir, translations, mode=mode)
        nav = build_nav(chapters)
        # 产物用**书名**命名，不用项目名：项目名是管理标识，产物是给人看的
        path = dest / f"{output_stem(ir)}.{mode}.epub"
        write_epub(path, title=ir.doc.title, author=ir.doc.author, language="zh",
                   chapters=chapters, css=CSS, nav=nav, images_dir=proj.assets,
                   cover_image=ir.cover_image(),
                   identifier=f"urn:transbook:{ir.doc.id}:{mode}")
        res.files.append(path)
        res.chapters = len(chapters)
    if "pdf" in wants:
        out = render_pdf(proj.dir, ir, translations, mode=mode,
                         name=f"{output_stem(ir)}.{mode}.typ")
        if out.error or out.pdf_path is None:
            res.error = out.error or "PDF 渲染失败"
        else:
            res.files.append(out.pdf_path)
    return res


# ── 端到端 ──────────────────────────────────────────────────────────
@dataclass
class FullResult:
    extract: dict[str, Any] = field(default_factory=dict)
    imported: str = ""
    translate: str = ""
    render: dict[str, Any] = field(default_factory=dict)


def run_full(proj: Project, source: Path, *, engine: str = "deepseek",
             model: str | None = None, base_url: str | None = None,
             price_tier: str = "peak", glossary: str | Path | None = None,
             max_cost: float = 0.0, mode: str = "bilingual", to: str = "epub",
             rolling_summary: bool = False, dry_run: bool = False,
             progress: Progress | None = None) -> FullResult:
    """一条龙：抽取 → 入库 → 翻译 → 渲染。"""
    def step(name: str, frac: float):
        if progress:
            progress(name, frac)

    step("抽取", 0.02)
    ex = run_extract(proj, source, doc_id=proj.doc_id)
    step("入库", 0.25)
    imp = run_import(proj)
    step("翻译", 0.30)
    rep = run_translate(proj, engine=engine, model=model, base_url=base_url,
                        price_tier=price_tier, glossary=glossary, max_cost=max_cost,
                        rolling_summary=rolling_summary, dry_run=dry_run,
                        progress=progress, band=(0.30, 0.92))
    step("渲染", 0.92)
    ren = run_render(proj, mode=mode, to=to)
    step("完成", 1.0)
    return FullResult(
        extract={"summary": ex.summary, "vertical": ex.vertical,
                 "headers_dropped": ex.headers_dropped, "ruby_stripped": ex.ruby_stripped},
        imported=str(imp), translate=rep.summary(), render=ren.as_dict())


def copy_source(proj: Project, source: Path) -> Path:
    """把一个已存在的输入文件复制进项目目录（供 CLI 之外的调用方使用）。"""
    ext = source.suffix.lower()
    if ext not in SOURCE_EXT:
        raise PipelineError(f"不支持的格式 {ext}")
    proj.dir.mkdir(parents=True, exist_ok=True)
    dst = proj.dir / f"source{ext}"
    if source.resolve() != dst.resolve():
        shutil.copyfile(source, dst)
    return dst
