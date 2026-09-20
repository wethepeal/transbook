"""transbook 命令行入口。

M0/M1 阶段命令：
  tp version / tp doctor        环境自检
  tp extract <book.epub> -o DIR  抽取为 DocumentIR + Markdown 预览
  tp preview <DIR|book.ir.json>  查看已抽取的结构
随里程碑推进逐步加入 translate / export-review / apply-review / render / status / retry。
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from transbook import __version__
from transbook.config import env_candidates, env_files_found, load_dotenv


def _tolerate_unencodable_output() -> None:
    """让标准输出遇到当前编码表达不了的字符时替换成 `?`，而不是把命令打断。

    为什么需要：stdout 被**重定向**（管道、写文件、CI 采集）时，Windows 上的 Python
    按 ANSI 代码页编码，简体中文机器上是 GBK——而 GBK 里没有 `✓`(U+2713)、
    `✗`(U+2717)、`⑪`(U+246A) 这些字符，rich 一打印就抛 UnicodeEncodeError，
    整条命令以非零码退出。直接输出到真实控制台时走的是控制台 Unicode API，
    没有这个问题，所以这个坑**只在 `tp ... | ...`、`tp ... > log.txt` 和 CI 里踩得到**
    （已实测：`rich` 打印 ✓ 到管道 → rc=1）。

    这是兜底而不是替代品：新增输出仍应优先用 GBK 有的字符。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):  # 已被包装过 / 不支持重配置
            pass


_tolerate_unencodable_output()

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="电子书翻译流水线：PDF/EPUB（英/日）→ 中文，输出 EPUB/PDF。",
)
console = Console()

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"transbook [bold]{__version__}[/bold]")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False,
        "--version",
        "-V",
        help="显示版本号并退出。",
        callback=_version_callback,
        is_eager=True,
    ),
) -> None:
    """电子书翻译流水线：PDF/EPUB（英/日）→ 中文，输出 EPUB/PDF。"""


@app.command()
def version() -> None:
    """显示版本号（等同于 `tp --version`）。"""
    console.print(f"transbook [bold]{__version__}[/bold]")


@app.command()
def doctor() -> None:
    """环境自检：解释器、目录、缓存重定向、密钥、磁盘余量。"""
    table = Table(title=f"transbook {__version__} · 环境自检", title_justify="left")
    table.add_column("项目", style="cyan", no_wrap=True)
    table.add_column("值", overflow="fold")

    table.add_row("Python", f"{sys.version.split()[0]}  ({sys.executable})")
    table.add_row("平台", f"{platform.system()} {platform.release()}")
    table.add_row("项目根", str(PROJECT_ROOT))
    table.add_row(".venv", "存在" if (PROJECT_ROOT / ".venv").is_dir() else "[yellow]不存在[/yellow]")

    loaded = load_dotenv()
    found = env_files_found()
    table.add_row(
        ".env",
        f"已加载 {len(loaded)} 项（{', '.join(sorted(loaded)) or '—'}）"
        if loaded
        else "[dim]无（可用 .env.example 生成）[/dim]",
    )
    # 装成 wheel 后 PROJECT_ROOT 不再是项目根，所以要说清楚到底读了哪一份
    table.add_row(
        ".env 位置",
        "\n".join(str(p) for p in found) if found
        else f"[dim]按顺序找过（均不存在）：{'; '.join(str(p) for p in env_candidates())}[/dim]",
    )

    for name in ("DSH_HOME", "UV_CACHE_DIR", "HF_HOME", "PIP_CACHE_DIR", "OLLAMA_MODELS"):
        value = os.environ.get(name)
        table.add_row(name, value if value else "[dim]未设置[/dim]")

    key = os.environ.get("DEEPSEEK_API_KEY")
    origin = "（来自 .env）" if "DEEPSEEK_API_KEY" in loaded else ""
    table.add_row(
        "DEEPSEEK_API_KEY",
        f"[green]已设置（{len(key)} 字符）{origin}[/green]"
        if key
        else "[yellow]未设置 → 复制 .env.example 为 .env 并填入[/yellow]",
    )

    for drive in ("C", "Z"):
        try:
            total, _used, free = shutil.disk_usage(f"{drive}:\\")
            style = "red" if (drive == "C" and free < 8 * 1024**3) else "green"
            table.add_row(
                f"{drive}: 磁盘",
                f"[{style}]可用 {free / 1024**3:.1f} GB[/{style}] / 共 {total / 1024**3:.0f} GB",
            )
        except OSError as exc:  # pragma: no cover - 仅诊断用
            table.add_row(f"{drive}: 磁盘", f"[red]{exc}[/red]")

    console.print(table)


@app.command()
def extract(
    source: Path = typer.Argument(..., help="输入的 .epub 或 .pdf 文件"),
    out: Path = typer.Option(Path("data/work/book"), "--out", "-o", help="输出目录"),
    limit: int | None = typer.Option(None, "--limit", help="预览只输出前 N 个块"),
    no_assets: bool = typer.Option(False, "--no-assets", help="不提取图片"),
    keep_headers: bool = typer.Option(False, "--keep-headers",
                                      help="不过滤页眉/页脚（PDF 页码、书眉）"),
    keep_ruby: bool = typer.Option(False, "--keep-ruby",
                                   help="不剥离 PDF 内联的振假名（注音）"),
    doc_id: str | None = typer.Option(None, "--doc-id", help="文档 ID（默认由书名生成短标识）"),
) -> None:
    """① 抽取：EPUB / PDF → DocumentIR(JSON) + Markdown 预览（人工检查闸门）。"""
    import inspect

    from transbook.ingest import EpubError, PdfError, ingestor_for
    from transbook.ingest.preview import summarize, to_markdown

    if not source.is_file():
        console.print(f"[red]文件不存在：{source}[/red]")
        raise typer.Exit(1)

    out.mkdir(parents=True, exist_ok=True)
    try:
        # `filter_headers` / `strip_ruby` 只有 PDF 抽取器用得上；EPUB 路径忽略
        ing = ingestor_for(source, doc_id=doc_id,
                           filter_headers=not keep_headers, strip_ruby=not keep_ruby)
        # EPUB 抽取器支持 assets_dir（提取图片）；PDF 抽取器暂不提取图片
        ir = (ing.extract(assets_dir=None if no_assets else out / "assets")
              if "assets_dir" in inspect.signature(ing.extract).parameters
              else ing.extract())
    except (EpubError, PdfError, ValueError) as exc:
        console.print(f"[red]抽取失败：{exc}[/red]")
        raise typer.Exit(2) from exc

    ir_path = out / "book.ir.json"
    ir_path.write_text(ir.model_dump_json(indent=2), encoding="utf-8")
    md_path = out / "preview.md"
    md_path.write_text(to_markdown(ir, limit=limit), encoding="utf-8")

    console.print(f"[green]✓[/green] {summarize(ir)}")
    console.print(f"  IR      : {ir_path}")
    console.print(f"  预览    : {md_path}   ← [bold]请先看这份再翻译[/bold]")
    if ir.doc.vertical:
        console.print("  [dim]检测到竖排（縦書き）：抽取顺序已按日文阅读顺序还原，"
                      "输出按中文横排排版[/dim]")
    dropped = getattr(getattr(ing, "stats", None), "headers_dropped", 0)
    if dropped:
        console.print(f"  [dim]已过滤页眉/页脚 {dropped} 段（页码、书眉）"
                      f"——要保留请加 --keep-headers[/dim]")
    ruby = getattr(getattr(ing, "stats", None), "ruby_stripped", 0)
    if ruby:
        console.print(f"  [dim]已剥离内联振假名 {ruby} 字（PDF 注音无标记，按字号识别）"
                      f"——要保留请加 --keep-ruby[/dim]")


@app.command()
def preview(
    target: Path = typer.Argument(..., help="IR 文件或包含 book.ir.json 的目录"),
    limit: int = typer.Option(60, "--limit", "-n", help="输出前 N 个块"),
    full: bool = typer.Option(False, "--full", help="输出全部块"),
) -> None:
    """查看已抽取的结构（Markdown）。"""
    from transbook.ingest.preview import to_markdown
    from transbook.ir import DocumentIR

    path = target / "book.ir.json" if target.is_dir() else target
    if not path.is_file():
        console.print(f"[red]找不到 IR：{path}[/red]")
        raise typer.Exit(1)
    ir = DocumentIR.model_validate(json.loads(path.read_text(encoding="utf-8")))
    console.print(to_markdown(ir, limit=None if full else limit))


def _resolve_db(target: Path) -> Path:
    """接受目录或直接的 .db 路径，统一解析为数据库文件路径。"""
    if target.is_dir():
        return target / "translations.db"
    return target


@app.command("import")
def import_cmd(
    ir_path: Path = typer.Argument(..., help="book.ir.json 或包含它的目录"),
    db: Path | None = typer.Option(None, "--db", help="SQLite 路径（默认与 IR 同目录的 translations.db）"),
    target_lang: str = typer.Option("zh", "--target-lang", help="目标语言"),
) -> None:
    """⑤ 入库：IR → SQLite 段落表（稳定 ID + 文本哈希 + TM 复用）。"""
    from transbook.ir import DocumentIR
    from transbook.store import connect, import_ir

    path = ir_path / "book.ir.json" if ir_path.is_dir() else ir_path
    if not path.is_file():
        console.print(f"[red]找不到 IR：{path}[/red]")
        raise typer.Exit(1)
    db_path = db or (path.parent / "translations.db")

    ir = DocumentIR.model_validate(json.loads(path.read_text(encoding="utf-8")))
    conn = connect(db_path)
    try:
        st = import_ir(conn, ir, target_lang=target_lang)
    finally:
        conn.close()
    console.print(f"[green]✓[/green] 入库完成：{st}")
    console.print(f"  数据库: {db_path}")


@app.command()
def status(
    target: Path = typer.Argument(Path("data/work/re0-v43"), help="工作目录或 .db 路径"),
) -> None:
    """查看翻译进度与成本。"""
    from transbook.store import connect, stats

    db_path = _resolve_db(target)
    if not db_path.is_file():
        console.print(f"[red]找不到数据库：{db_path}[/red]")
        raise typer.Exit(1)
    conn = connect(db_path)
    try:
        s = stats(conn)
    finally:
        conn.close()

    table = Table(title=f"翻译进度 · {db_path.name}", title_justify="left", expand=False)
    table.add_column("项", style="cyan", width=12)
    table.add_column("值", overflow="ellipsis", max_width=96)
    for d in s["docs"]:
        table.add_row(f"文档", f"{d['id']} ｜ {d['title'] or '(无标题)'} ｜ {d['source_lang']} ｜ "
                               f"{d['block_count']} 块")
    table.add_row("段落总数", str(s["n"]))
    table.add_row("已机翻", f"{s['done']}（{s['done'] / max(s['n'], 1) * 100:.1f}%）")
    table.add_row("已审核定稿", str(s["reviewed"]))
    table.add_row("状态分布", " ｜ ".join(f"{k} {v}" for k, v in s["by_status"].items()) or "—")
    table.add_row("Token", f"输入 {s['tin']:,} ｜ 输出 {s['tout']:,}")
    table.add_row("累计成本", f"[yellow]¥{s['cost']:.4f}[/yellow]")
    table.add_row("TM 条目", str(s["tm_entries"]))
    console.print(table)


def _load_glossary(path: Path | None) -> dict[str, str]:
    """术语表：`.json`（对象）或每行 `原文=译文` 的文本。零额外依赖。"""
    if path is None:
        return {}
    if not path.is_file():
        console.print(f"[yellow]术语表不存在，忽略：{path}[/yellow]")
        return {}
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        data = json.loads(text)
        return {str(k): str(v) for k, v in data.items()}
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip()
    return out


@app.command()
def translate(
    target: Path = typer.Argument(Path("data/work/re0-v43"), help="工作目录或 .db 路径"),
    engine: str = typer.Option("fake", "--engine", help="fake（零成本）｜ deepseek ｜ local（OpenAI 兼容）"),
    model: str = typer.Option("", "--model", help="模型名（默认取 .env 或引擎默认）"),
    base_url: str = typer.Option("", "--base-url", help="local 引擎端点，如 http://127.0.0.1:8080/v1"),
    limit: int | None = typer.Option(None, "--limit", help="只译前 N 段（调试用）"),
    max_cost: float = typer.Option(0.5, "--max-cost", help="成本硬上限（元），0 = 不限"),
    batch_chars: int = typer.Option(2400, "--batch-chars", help="每批字符预算"),
    batch_items: int = typer.Option(24, "--batch-items", help="每批段落数上限"),
    dry_run: bool = typer.Option(False, "--dry-run", help="只分批并预估费用，**不调用任何 API**"),
    glossary: Path | None = typer.Option(None, "--glossary", help="术语表（.json 或每行 原文=译文）"),
    target_lang: str = typer.Option("zh", "--target-lang"),
    price_tier: str = typer.Option("peak", "--price-tier", help="peak（保守）｜ idle"),
    rolling_summary: bool = typer.Option(False, "--rolling-summary",
                                         help="按章注入前情提要（先跑 tp summarize）"),
) -> None:
    """③ 翻译：段落 → 译文（可断点续跑、有成本护栏）。"""
    from transbook.config import get
    from transbook.store import connect
    from transbook.translate import BookContext, DeepSeekProvider, FakeProvider, run
    from transbook.translate.deepseek import DEFAULT_BASE_URL

    db_path = _resolve_db(target)
    if not db_path.is_file():
        console.print(f"[red]找不到数据库：{db_path}[/red]")
        raise typer.Exit(1)
    conn = connect(db_path)

    if engine == "fake":
        provider = FakeProvider()
    elif engine in ("deepseek", "local"):
        key = get("DEEPSEEK_API_KEY") or ""
        if not key:
            if not dry_run:
                console.print("[red]未配置 DEEPSEEK_API_KEY：请复制 .env.example 为 .env 并填入密钥[/red]")
                raise typer.Exit(2)
            key = "dry-run-placeholder"  # 干跑不会发请求，允许无密钥预估
        default_model = get("TRANSLATE_MODEL") or ("deepseek-flash" if engine == "deepseek" else "")
        # 关闭思考模式——两种引擎都需要，原因不同（都是 M0/M1 实测踩过的坑）：
        #   DeepSeek：思考模式**默认开启**（effort=high），思维链会白花 token 且拖慢；
        #   本地 Qwen3：不关会耗尽 token 且 content 返回空字符串。
        if engine == "local":
            extra = {"chat_template_kwargs": {"enable_thinking": False}}
        else:
            extra = {"thinking": {"type": "disabled"}}
        provider = DeepSeekProvider(
            key,
            model=model or default_model or "deepseek-flash",
            base_url=base_url or get("DEEPSEEK_BASE_URL") or DEFAULT_BASE_URL,
            # 本地端点不计费：沿用 API 单价会让成本护栏凭虚假费用提前停掉
            price_tier="local" if engine == "local" else price_tier,
            extra_body=extra,
        )
    else:
        console.print(f"[red]未知引擎：{engine}（可选 fake / deepseek / local）[/red]")
        raise typer.Exit(2)

    doc = conn.execute("SELECT * FROM doc LIMIT 1").fetchone()
    ctx = BookContext(
        doc_id=doc["id"] if doc else "",
        title=doc["title"] if doc else "",
        author=doc["author"] if doc else "",
        source_lang=(doc["source_lang"] if doc else "") or "ja",
        target_lang=target_lang,
        glossary=_load_glossary(glossary),
    )
    if ctx.glossary:
        console.print(f"术语表：{len(ctx.glossary)} 条")

    ir = None
    if rolling_summary:
        from transbook.ir import DocumentIR

        ir_path = target.parent / "book.ir.json" if target.is_file() else target / "book.ir.json"
        if not ir_path.is_file():
            console.print(f"[red]--rolling-summary 需要 book.ir.json：{ir_path}[/red]")
            raise typer.Exit(2)
        ir = DocumentIR.model_validate(json.loads(ir_path.read_text(encoding="utf-8")))
        n = len(conn.execute("SELECT 1 FROM chapter_summary WHERE doc_id=? LIMIT 1",
                             (ctx.doc_id,)).fetchall())
        if not n:
            console.print("[yellow]还没有前情提要——先跑 `tp summarize`，本次将不注入[/yellow]")
        else:
            console.print("  [dim]已启用滚动摘要（按章注入前情）[/dim]")

    try:
        rep = run(conn, provider, ctx, limit=limit, max_cost=max_cost,
                  batch_chars=batch_chars, batch_items=batch_items, dry_run=dry_run,
                  ir=ir, rolling_summary=rolling_summary,
                  progress=(None if dry_run else lambda m, f: console.print(f"  [dim]{m}[/dim]")))
    finally:
        conn.close()
    console.print(("[green]✓[/green] " if not dry_run else "[cyan]◦[/cyan] ") + rep.summary())


@app.command()
def summarize(
    target: Path = typer.Argument(..., help="工作目录（含 book.ir.json 与 translations.db）"),
    engine: str = typer.Option("deepseek", "--engine", help="deepseek / local / fake"),
    model: str | None = typer.Option(None, "--model"),
    base_url: str | None = typer.Option(None, "--base-url", help="本地 OpenAI 兼容端点"),
    budget: int = typer.Option(800, "--budget", help="注入前情的总字数上限"),
    window: int = typer.Option(4, "--window", help="注入最近几章的摘要"),
    chapter_chars: int = typer.Option(6000, "--chapter-chars", help="每章送入的原文上限"),
    force: bool = typer.Option(False, "--force", help="全部重新生成（默认沿用已有）"),
    price_tier: str = typer.Option("peak", "--price-tier"),
) -> None:
    """⑩ 滚动摘要：按章生成短摘要，供 `tp translate --rolling-summary` 注入前情。"""
    from transbook.config import get
    from transbook.ir import DocumentIR
    from transbook.store import connect
    from transbook.translate import BookContext, DeepSeekProvider, FakeProvider
    from transbook.translate.deepseek import DEFAULT_BASE_URL
    from transbook.translate.summary import generate_summaries

    work = target if target.is_dir() else target.parent
    ir_path = work / "book.ir.json"
    db_path = _resolve_db(target)
    if not ir_path.is_file() or not db_path.is_file():
        console.print(f"[red]缺少 book.ir.json 或 translations.db：{work}[/red]")
        raise typer.Exit(1)
    ir = DocumentIR.model_validate(json.loads(ir_path.read_text(encoding="utf-8")))

    if engine == "fake":
        provider = FakeProvider()
    elif engine in ("deepseek", "local"):
        key = get("DEEPSEEK_API_KEY") or ""
        if not key:
            console.print("[red]未配置 DEEPSEEK_API_KEY[/red]")
            raise typer.Exit(2)
        extra = ({"chat_template_kwargs": {"enable_thinking": False}} if engine == "local"
                 else {"thinking": {"type": "disabled"}})
        provider = DeepSeekProvider(
            key, model=model or get("TRANSLATE_MODEL") or "deepseek-flash",
            base_url=base_url or get("DEEPSEEK_BASE_URL") or DEFAULT_BASE_URL,
            price_tier=price_tier, extra_body=extra)
    else:
        console.print(f"[red]未知引擎：{engine}[/red]")
        raise typer.Exit(2)

    ctx = BookContext(doc_id=ir.doc.id, title=ir.doc.title, author=ir.doc.author,
                      source_lang=ir.doc.source_lang or "ja")
    conn = connect(db_path)
    try:
        rep = generate_summaries(conn, provider, ir, ctx, budget=budget, window=window,
                                 chapter_chars=chapter_chars, force=force,
                                 progress=lambda m, f: console.print(f"  [dim]{m}[/dim]"))
    finally:
        conn.close()
    console.print(f"[green]✓[/green] {rep.summary()}")


@app.command()
def render(
    target: Path = typer.Argument(Path("data/work/re0-v43"), help="工作目录（含 book.ir.json 与 translations.db）"),
    mode: str = typer.Option("bilingual", "--mode", "-m", help="bilingual（对照，供审核）｜ zh（纯中文终版）"),
    to: str = typer.Option("epub", "--to", help="epub ｜ pdf ｜ both"),
    out_dir: Path | None = typer.Option(None, "--out", "-o", help="输出目录（默认工作目录）"),
) -> None:
    """⑥ 渲染：IR + 译文 → EPUB / PDF（双语对照 / 纯中文）。"""
    from transbook.ir import DocumentIR
    from transbook.render import CSS, build_chapters, build_nav, render_pdf, write_epub
    from transbook.store import connect

    if mode not in ("bilingual", "zh"):
        console.print(f"[red]未知模式：{mode}（可选 bilingual / zh）[/red]")
        raise typer.Exit(2)
    wants = {"epub": ("epub",), "pdf": ("pdf",), "both": ("epub", "pdf")}.get(to)
    if wants is None:
        console.print(f"[red]未知输出：{to}（可选 epub / pdf / both）[/red]")
        raise typer.Exit(2)

    work = target if target.is_dir() else target.parent
    ir_path = work / "book.ir.json"
    db_path = _resolve_db(target)
    if not ir_path.is_file() or not db_path.is_file():
        console.print(f"[red]缺少 book.ir.json 或 translations.db：{work}[/red]")
        raise typer.Exit(1)

    ir = DocumentIR.model_validate(json.loads(ir_path.read_text(encoding="utf-8")))
    conn = connect(db_path)
    try:
        rows = conn.execute(
            "SELECT seg_id, COALESCE(final_translation, translation) AS t "
            "FROM segment WHERE COALESCE(final_translation, translation) IS NOT NULL"
        ).fetchall()
    finally:
        conn.close()
    # 键是**翻译单元 id**：`{doc_id}:{unit_id}`；普通块 unit_id 就是 block_id，
    # 表格则是 `{block_id}:r0c1` 这样的单元格子 id。
    translations = {r["seg_id"].split(":", 1)[1]: r["t"] for r in rows}

    translatable = len(ir.translatable())
    covered = sum(1 for b in ir.translatable()
                  if all(translations.get(u) for u in b.unit_ids()))
    dest = Path(out_dir) if out_dir else work
    dest.mkdir(parents=True, exist_ok=True)

    if "epub" in wants:
        chapters = build_chapters(ir, translations, mode=mode)
        nav = build_nav(chapters)
        epub_path = dest / f"{ir.doc.id}.{mode}.epub"
        cover = ir.cover_image()
        write_epub(epub_path, title=ir.doc.title, author=ir.doc.author, language="zh",
                   chapters=chapters, css=CSS, nav=nav,
                   images_dir=work / "assets", cover_image=cover,
                   identifier=f"urn:transbook:{ir.doc.id}:{mode}")
        tip = f" ｜ 封面 {cover}" if cover else " ｜ [yellow]未找到封面图[/yellow]"
        console.print(f"[green]✓[/green] EPUB({mode})：{epub_path} "
                      f"｜ {len(chapters)} 章 ｜ {epub_path.stat().st_size / 1024 / 1024:.2f} MB{tip}")

    if "pdf" in wants:
        res = render_pdf(work, ir, translations, mode=mode)
        if res.error or res.pdf_path is None:
            console.print(f"[red]✗ PDF 渲染失败：{res.error}[/red]")
            console.print(f"  Typst 源码已生成，可人工检查：{res.typ_path}")
        else:
            console.print(f"[green]✓[/green] PDF({mode})：{res.pdf_path} "
                          f"｜ {res.chapters} 章 / {res.paragraphs} 段 / {res.images} 图 "
                          f"/ {res.tables} 表 / {res.footnotes} 注 "
                          f"｜ {res.pdf_path.stat().st_size / 1024:.0f} KB")

    console.print(f"  段落覆盖 {covered}/{translatable}"
                  f"（{covered / max(translatable, 1) * 100:.1f}%）")
    if covered < translatable:
        console.print(f"  [yellow]提示：还有 {translatable - covered} 段没有译文，"
                      f"先跑 `tp translate` 可补齐[/yellow]")


@app.command("export-review")
def export_review_cmd(
    target: Path = typer.Argument(Path("data/work/re0-v43"), help="工作目录或 .db 路径"),
    fmt: str = typer.Option("tsv", "--format", "-f", help="tsv（可编辑，用于回灌）｜ md（只读通读）"),
    out: Path | None = typer.Option(None, "--out", "-o", help="输出路径"),
    only_translated: bool = typer.Option(False, "--only-translated", help="只导出已有译文的段落"),
) -> None:
    """⑦a 导出校对文件：改完译文后用 `tp apply-review` 回灌。"""
    from transbook.review import export_markdown, export_tsv
    from transbook.store import connect

    db_path = _resolve_db(target)
    if not db_path.is_file():
        console.print(f"[red]找不到数据库：{db_path}[/red]")
        raise typer.Exit(1)
    work = target if target.is_dir() else target.parent
    if fmt == "tsv":
        path = out or (work / "review.tsv")
        n = export_tsv(connect(db_path), path, only_translated=only_translated)
    elif fmt == "md":
        path = out or (work / "review.md")
        n = export_markdown(connect(db_path), path, only_translated=only_translated)
    else:
        console.print(f"[red]未知格式：{fmt}（可选 tsv / md）[/red]")
        raise typer.Exit(2)
    console.print(f"[green]✓[/green] 导出 {n} 段 → {path}")
    if fmt == "tsv":
        console.print("  只改第 4 列（translation），然后："
                      f"[bold]tp apply-review {work} {path.name}[/bold]")


@app.command("apply-review")
def apply_review_cmd(
    target: Path = typer.Argument(..., help="工作目录或 .db 路径"),
    file: Path = typer.Argument(..., help="校对文件（TSV）"),
    no_backup: bool = typer.Option(False, "--no-backup", help="不回灌前备份数据库（不建议）"),
) -> None:
    """⑦b 回灌校对结果：只写被修改过的行，定稿写入 `final_translation`（不覆盖机翻）。"""
    from transbook.review import apply_tsv
    from transbook.store import connect

    db_path = _resolve_db(target)
    if not db_path.is_file():
        console.print(f"[red]找不到数据库：{db_path}[/red]")
        raise typer.Exit(1)
    conn = connect(db_path)
    try:
        st = apply_tsv(conn, file, db_path=db_path, backup=not no_backup)
    except FileNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    finally:
        conn.close()
    console.print(f"[green]✓[/green] 回灌完成：{st.summary()}")


@app.command("clear-review")
def clear_review_cmd(
    target: Path = typer.Argument(..., help="工作目录或 .db 路径"),
    yes: bool = typer.Option(False, "--yes", "-y", help="确认执行"),
) -> None:
    """回滚：清空人工定稿，回到机翻状态（单段级回滚手段）。"""
    from transbook.review import clear_final
    from transbook.store import connect

    db_path = _resolve_db(target)
    if not db_path.is_file():
        console.print(f"[red]找不到数据库：{db_path}[/red]")
        raise typer.Exit(1)
    if not yes:
        console.print("[yellow]这会清空所有人工定稿（机翻保留）。确认请加 --yes[/yellow]")
        raise typer.Exit(1)
    conn = connect(db_path)
    try:
        n = clear_final(conn)
    finally:
        conn.close()
    console.print(f"[green]✓[/green] 已清空 {n} 段定稿，回到机翻状态")


@app.command()
def terms(
    target: Path = typer.Argument(Path("data/work/re0-v43"), help="工作目录或 .db 路径"),
    min_count: int = typer.Option(3, "--min-count", help="候选词最少出现次数"),
    top: int = typer.Option(400, "--top", help="最多输出多少条候选"),
    out: Path | None = typer.Option(None, "--out", "-o", help="输出 TSV（默认 <workdir>/terms.candidates.tsv）"),
    no_kanji: bool = typer.Option(False, "--no-kanji", help="只抽片假名与引号短语（汉字噪声大）"),
) -> None:
    """⑧a 术语预扫描：抽候选人名/专有名词 → 你填译法 → 翻译时强制注入。"""
    from transbook.quality import extract_candidates, write_candidates
    from transbook.store import connect

    db_path = _resolve_db(target)
    if not db_path.is_file():
        console.print(f"[red]找不到数据库：{db_path}[/red]")
        raise typer.Exit(1)
    conn = connect(db_path)
    try:
        texts = [r["source_text"] for r in
                 conn.execute("SELECT source_text FROM segment ORDER BY doc_id, ord")]
    finally:
        conn.close()

    cands = extract_candidates(texts, min_count=min_count, top=top, include_kanji=not no_kanji)
    work = target if target.is_dir() else target.parent
    path = out or (work / "terms.candidates.tsv")
    write_candidates(path, cands)
    by_kind: dict[str, int] = {}
    for c in cands:
        by_kind[c.kind] = by_kind.get(c.kind, 0) + 1
    console.print(f"[green]✓[/green] 候选 {len(cands)} 条 → {path}")
    console.print("  分布：" + " ｜ ".join(f"{k} {v}" for k, v in sorted(by_kind.items())))
    console.print("  前 8 条：" + "，".join(f"{c.term}({c.count})" for c in cands[:8]))
    console.print("  填好最后一列后传给："
                  f"[bold]tp translate {work} --glossary {path.name}[/bold]")


@app.command()
def qa(
    target: Path = typer.Argument(Path("data/work/re0-v43"), help="工作目录或 .db 路径"),
    glossary: Path | None = typer.Option(None, "--glossary", help="术语表（用于一致性检查）"),
    strict: bool = typer.Option(False, "--strict", help="有错误时以非零退出码结束（可进 CI）"),
    examples: int = typer.Option(8, "--examples", help="每类问题展示几个例子（0=全部）"),
) -> None:
    """⑧b 译文 QA：漏译 / 原文残留 / 假名残留 / 术语不一致 / 长度异常。"""
    from transbook.ir import DocumentIR
    from transbook.quality import check, load_glossary
    from transbook.store import connect

    work = target if target.is_dir() else target.parent
    db_path = _resolve_db(target)
    if not db_path.is_file():
        console.print(f"[red]找不到数据库：{db_path}[/red]")
        raise typer.Exit(1)
    ir = None
    ir_path = work / "book.ir.json"
    if ir_path.is_file():
        ir = DocumentIR.model_validate(json.loads(ir_path.read_text(encoding="utf-8")))
    gl = load_glossary(glossary)
    conn = connect(db_path)
    try:
        rep = check(conn, ir, gl)
    finally:
        conn.close()

    console.print(f"[bold]QA 报告[/bold] · {rep.summary()}")
    if gl:
        console.print(f"  术语表：{len(gl)} 条")
    shown: dict[str, int] = {}
    for issue in rep.issues:
        limit = examples if examples else 10**9
        if shown.get(issue.kind, 0) >= limit:
            continue
        shown[issue.kind] = shown.get(issue.kind, 0) + 1
        console.print(str(issue))
    for kind, n in rep.counts().items():
        if examples and n > examples:
            console.print(f"  … [{kind}] 另有 {n - examples} 条未显示")
    if strict and not rep.ok():
        raise typer.Exit(1)


@app.command()
def validate(
    target: Path = typer.Argument(..., help="EPUB 文件，或含 *.epub 的工作目录"),
    examples: int = typer.Option(12, "--examples", help="最多展示几条问题"),
    strict: bool = typer.Option(False, "--strict", help="有错误时以非零码退出（可进 CI）"),
) -> None:
    """⑨ EPUB 校验：内置结构检查 + epubcheck（若已安装）。"""
    from transbook.validate import find_epubcheck, validate_epub

    if target.is_dir():
        epubs = sorted(target.glob("*.epub"))
        if not epubs:
            console.print(f"[red]目录里没有 .epub：{target}[/red]")
            raise typer.Exit(1)
    elif target.is_file():
        epubs = [target]
    else:
        console.print(f"[red]找不到目标：{target}[/red]")
        raise typer.Exit(1)

    jar = find_epubcheck()
    console.print(f"[dim]epubcheck：{jar if jar else '未安装（仅跑内置检查）'}[/dim]")
    bad = 0
    for epub in epubs:
        rep = validate_epub(epub)
        console.print(f"\n[bold]✓ {epub.name}[/bold]" if not rep.errors
                      else f"\n[bold red]✗ {epub.name}[/bold red]")
        console.print(f"  {rep.summary()}")
        for issue in rep.issues[:examples]:
            colour = "red" if issue.level == "error" else "yellow"
            console.print(f"  [{colour}]{issue}[/{colour}]")
        if len(rep.issues) > examples:
            console.print(f"  … 另有 {len(rep.issues) - examples} 条未显示")
        bad += 1 if rep.errors else 0
    if bad and strict:
        raise typer.Exit(1)


@app.command()
def compare(
    target: Path = typer.Argument(..., help="工作目录（含 book.ir.json）"),
    chapter: int = typer.Option(0, "--chapter", help="只比这一章（0=全书按顺序取样）"),
    limit: int = typer.Option(40, "--limit", help="最多比较多少段"),
    engine_b: str = typer.Option("local", "--engine-b", help="对照引擎（默认本地）"),
    model_b: str | None = typer.Option(None, "--model-b"),
    base_url: str | None = typer.Option("http://127.0.0.1:8117/v1", "--base-url-b",
                                        help="本地 OpenAI 兼容端点"),
    engine_a: str = typer.Option("deepseek", "--engine-a"),
    model_a: str | None = typer.Option(None, "--model-a"),
    sample: int = typer.Option(4, "--sample", help="展示几条逐条对照"),
    batch_items: int = typer.Option(16, "--batch-items"),
    price_tier: str = typer.Option("idle", "--price-tier"),
) -> None:
    """引擎对比：同一批段落跑两个引擎，看成本/速度/可判定质量。"""
    from transbook.config import get
    from transbook.ir import DocumentIR
    from transbook.quality import compare as run_compare
    from transbook.translate import BookContext, DeepSeekProvider
    from transbook.translate.base import SegmentIn
    from transbook.translate.deepseek import DEFAULT_BASE_URL
    from transbook.translate.summary import chapter_spans

    work = target if target.is_dir() else target.parent
    ir_path = work / "book.ir.json"
    if not ir_path.is_file():
        console.print(f"[red]缺少 book.ir.json：{work}[/red]")
        raise typer.Exit(1)
    ir = DocumentIR.model_validate(json.loads(ir_path.read_text(encoding="utf-8")))

    blocks = ir.translatable()
    if chapter:
        spans = [s for s in chapter_spans(ir) if s.index == chapter]
        if not spans:
            console.print(f"[red]没有第 {chapter} 章（可用章号："
                          f"{[s.index for s in chapter_spans(ir)]}）[/red]")
            raise typer.Exit(1)
        wanted = {b.id for b in spans[0].blocks}
        blocks = [b for b in blocks if b.id in wanted]
    items: list[SegmentIn] = []
    for b in blocks[:limit]:
        for uid in b.unit_ids():
            t = b.unit_text(uid)
            if t.strip():
                items.append(SegmentIn(uid, t, b.type))
    if not items:
        console.print("[red]没有可比较的段落[/red]")
        raise typer.Exit(1)

    key = get("DEEPSEEK_API_KEY") or ""
    local_key = get("LOCAL_API_KEY") or "sk-local"  # llama.cpp 默认不校验
    providers = [DeepSeekProvider(key or "no-key", model=model_a or "deepseek-flash",
                                 base_url=get("DEEPSEEK_BASE_URL") or DEFAULT_BASE_URL,
                                 price_tier=price_tier,
                                 extra_body={"thinking": {"type": "disabled"}})]
    providers.append(DeepSeekProvider(  # 本地端点也是 OpenAI 兼容协议
        local_key, model=model_b or "qwen3-8b",
        base_url=(base_url or "").rstrip("/"),
        price_tier="local",  # 本地不计费，否则对比表会算出虚假花费
        extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        timeout=600.0))
    providers[1].name = "local"

    ctx = BookContext(doc_id=ir.doc.id, title=ir.doc.title, author=ir.doc.author,
                      source_lang=ir.doc.source_lang or "ja")
    console.print(f"[dim]对比 {len(items)} 段（第 {chapter or '全书'} 章区间）…[/dim]")
    rep = run_compare(items, providers, ctx, sample=sample, batch_items=batch_items,
                      progress=lambda m, f: console.print(f"  [dim]{m}[/dim]"))

    console.print(f"\n[bold]成本与速度[/bold]\n{rep.table()}")
    console.print("\n[bold]可判定质量问题[/bold]（数字越小越好）")
    kinds = sorted({k for r in rep.results for k in r.issues})
    if kinds:
        header = f"{'引擎':<12}" + "".join(f"{k:>16}" for k in kinds)
        console.print(header)
        for r in rep.results:
            console.print(f"{r.name:<12}" + "".join(f"{r.issues.get(k, 0):>16}" for k in kinds))
    else:
        console.print("  （两边都没有可判定问题）")
    for r in rep.results:
        if r.error:
            console.print(f"  [yellow]{r.name} 出错：{r.error}[/yellow]")

    if rep.samples:
        console.print("\n[bold]逐条对照[/bold]")
        for seg_id, src, outs in rep.samples:
            console.print(f"\n  [dim]{seg_id}[/dim] 原文：{src[:110]}")
            for name, tgt in outs.items():
                console.print(f"    [cyan]{name}[/cyan]：{tgt[:110] or '（空）'}")
    console.print("\n[dim]质量指标只覆盖可判定问题；语义质量仍需人眼看上面几条对照。[/dim]")


def _write_env_key(target: Path, key: str) -> None:
    """把 `DEEPSEEK_API_KEY` 写进 `.env`，保留文件里其它内容。"""
    lines = target.read_text(encoding="utf-8").splitlines() if target.is_file() else []
    out: list[str] = []
    replaced = False
    for line in lines:
        if line.strip().startswith("DEEPSEEK_API_KEY"):
            out.append(f"DEEPSEEK_API_KEY={key}")
            replaced = True
        else:
            out.append(line)
    if not replaced:
        if out and out[-1].strip():
            out.append("")
        out.append(f"DEEPSEEK_API_KEY={key}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(out) + "\n", encoding="utf-8")


@app.command()
def setup(
    key: str = typer.Option("", "--key", help="直接给出密钥，跳过交互询问"),
    yes: bool = typer.Option(False, "--yes", "-y", help="非交互：没配置也不询问，直接跳过"),
    force: bool = typer.Option(False, "--force", help="已配置也重新询问"),
) -> None:
    """首次配置：把 DeepSeek API Key 写进 .env。

    发布包里的 start.cmd 会自动调用它，所以从压缩包安装的用户不必手敲命令。
    之所以把这件"要显示中文"的事从批处理挪到 Python：批处理文件里的中文在不同
    代码页下会乱码，而 Python 在 Windows 上走控制台 Unicode API，怎么都不会乱。
    """
    existing = next((p for p in env_candidates() if p.is_file()), None)
    if existing is None:
        # 源码树里写项目根；装成 wheel 后 PROJECT_ROOT 不再是项目根，就写当前目录
        root = PROJECT_ROOT if (PROJECT_ROOT / "pyproject.toml").is_file() else Path.cwd()
        existing = root / ".env"

    load_dotenv()
    current = (os.environ.get("DEEPSEEK_API_KEY") or "").strip()

    if not key and current and not force:
        console.print(f"[green]已配置密钥[/green]（{existing}，{len(current)} 字符），跳过。")
        console.print("[dim]要换密钥：tp setup --force[/dim]")
        return
    if not key and yes:
        console.print(f"[yellow]未配置密钥，已跳过。[/yellow]编辑 {existing} 补上即可。")
        return
    if not key:
        console.print("\n需要一个 DeepSeek API Key 才能翻译。申请地址：")
        console.print("  https://platform.deepseek.com/api_keys\n")
        key = typer.prompt("粘贴 API Key（直接回车跳过）", default="",
                           show_default=False).strip()

    if not key:
        console.print("[yellow]已跳过。[/yellow]没有密钥也能启动，但只能用 Fake 引擎空跑流程。")
        console.print(f"  以后补上：编辑 {existing}")
        return

    _write_env_key(existing, key)
    console.print(f"[green]已写入[/green] {existing}")


def _open_browser_soon(url: str, delay: float = 1.5) -> None:
    """延迟一会儿再开浏览器。

    立刻打开会撞上"uvicorn 还没开始监听"，用户看到的是连接失败页，
    比不自动打开还糟。1.5 秒在实测里足够。
    """
    import threading
    import webbrowser

    timer = threading.Timer(delay, lambda: webbrowser.open(url))
    timer.daemon = True
    timer.start()


@app.command()
def serve(
    root: Path = typer.Option(Path("data/work"), "--root", help="项目根目录（下辖各本书）"),
    host: str = typer.Option("127.0.0.1", "--host", help="监听地址（默认只本机）"),
    port: int = typer.Option(8321, "--port"),
    log_level: str = typer.Option("info", "--log-level"),
    open_browser: bool = typer.Option(False, "--open/--no-open", help="就绪后自动打开浏览器"),
) -> None:
    """启动 HTTP 服务（M5）：提交一本书 / 查进度(SSE) / 交审核。"""
    try:
        import uvicorn
    except ImportError:  # pragma: no cover
        console.print("[red]未安装 uvicorn（uv add fastapi 'uvicorn[standard]'）[/red]")
        raise typer.Exit(2) from None
    from transbook.service import NO_WEB_HINT, create_app

    root = root if root.is_absolute() else (Path.cwd() / root)
    root.mkdir(parents=True, exist_ok=True)
    app = create_app(root)
    web = getattr(app.state, "web_dir", None)
    console.print(f"[green]transbook 服务[/green] http://{host}:{port}")
    if web:
        console.print(f"  界面    http://{host}:{port}/ ｜ 项目根 {root}")
    else:
        console.print(f"  [yellow]界面未构建[/yellow]：{NO_WEB_HINT}")
    console.print(f"  接口文档 http://{host}:{port}/docs")
    console.print("  [dim]作业在独立子进程里跑，服务重启不影响已提交的作业[/dim]")
    if open_browser and web:
        _open_browser_soon(f"http://{'127.0.0.1' if host in ('0.0.0.0', '::') else host}:{port}/")
    uvicorn.run(app, host=host, port=port, log_level=log_level)


if __name__ == "__main__":  # pragma: no cover
    app()
