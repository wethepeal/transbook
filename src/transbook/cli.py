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
from transbook.config import load_dotenv

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
    table.add_row(
        ".env",
        f"已加载 {len(loaded)} 项（{', '.join(sorted(loaded)) or '—'}）"
        if loaded
        else "[dim]无（可用 .env.example 生成）[/dim]",
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
    source: Path = typer.Argument(..., help="输入的 EPUB 文件（PDF 支持在 M2 加入）"),
    out: Path = typer.Option(Path("data/work/book"), "--out", "-o", help="输出目录"),
    limit: int | None = typer.Option(None, "--limit", help="预览只输出前 N 个块"),
    no_assets: bool = typer.Option(False, "--no-assets", help="不提取图片"),
    doc_id: str | None = typer.Option(None, "--doc-id", help="文档 ID（默认由书名生成短标识）"),
) -> None:
    """① 抽取：EPUB → DocumentIR(JSON) + Markdown 预览（人工检查闸门）。"""
    from transbook.ingest import EpubError, EpubIngestor
    from transbook.ingest.preview import summarize, to_markdown

    if not source.is_file():
        console.print(f"[red]文件不存在：{source}[/red]")
        raise typer.Exit(1)

    out.mkdir(parents=True, exist_ok=True)
    try:
        ing = EpubIngestor(source, doc_id=doc_id)
        ir = ing.extract(assets_dir=None if no_assets else out / "assets")
    except EpubError as exc:
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
        console.print("  [yellow]提示：检测到竖排样式（输出将按中文横排排版）[/yellow]")


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
        # 本地 Qwen3 等思考型模型必须关闭思考，否则 token 耗尽且返回空内容（M0 实测）
        extra = {"chat_template_kwargs": {"enable_thinking": False}} if engine == "local" else {}
        provider = DeepSeekProvider(
            key,
            model=model or default_model or "deepseek-flash",
            base_url=base_url or get("DEEPSEEK_BASE_URL") or DEFAULT_BASE_URL,
            price_tier=price_tier,
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

    try:
        rep = run(conn, provider, ctx, limit=limit, max_cost=max_cost,
                  batch_chars=batch_chars, batch_items=batch_items, dry_run=dry_run,
                  progress=(None if dry_run else lambda m: console.print(f"  [dim]{m}[/dim]")))
    finally:
        conn.close()
    console.print(("[green]✓[/green] " if not dry_run else "[cyan]◦[/cyan] ") + rep.summary())


@app.command()
def render(
    target: Path = typer.Argument(Path("data/work/re0-v43"), help="工作目录（含 book.ir.json 与 translations.db）"),
    mode: str = typer.Option("bilingual", "--mode", "-m", help="bilingual（对照，供审核）｜ zh（纯中文终版）"),
    out: Path | None = typer.Option(None, "--out", "-o", help="输出 .epub 路径"),
) -> None:
    """⑥ 渲染：IR + 译文 → EPUB（双语对照 / 纯中文）。"""
    from transbook.ir import DocumentIR
    from transbook.render import CSS, build_chapters, build_nav, write_epub
    from transbook.store import connect

    if mode not in ("bilingual", "zh"):
        console.print(f"[red]未知模式：{mode}（可选 bilingual / zh）[/red]")
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
            "SELECT block_id, COALESCE(final_translation, translation) AS t "
            "FROM segment WHERE COALESCE(final_translation, translation) IS NOT NULL"
        ).fetchall()
    finally:
        conn.close()
    translations = {r["block_id"]: r["t"] for r in rows}

    chapters = build_chapters(ir, translations, mode=mode)
    nav = build_nav(chapters)
    out_path = out or (work / f"{ir.doc.id}.{mode}.epub")
    write_epub(out_path, title=ir.doc.title, author=ir.doc.author,
               language="zh", chapters=chapters, css=CSS, nav=nav,
               images_dir=work / "assets", identifier=f"urn:transbook:{ir.doc.id}:{mode}")

    translatable = len(ir.translatable())
    covered = sum(1 for b in ir.translatable() if translations.get(b.id))
    size_mb = out_path.stat().st_size / 1024 / 1024
    console.print(f"[green]✓[/green] 已生成 {mode} 版 EPUB：{out_path}")
    console.print(f"  章节 {len(chapters)} ｜ 段落覆盖 {covered}/{translatable}"
                  f"（{covered / max(translatable, 1) * 100:.1f}%）｜ 体积 {size_mb:.2f} MB")
    if covered < translatable:
        console.print(f"  [yellow]提示：还有 {translatable - covered} 段没有译文，"
                      f"先跑 `tp translate` 可补齐[/yellow]")


if __name__ == "__main__":  # pragma: no cover
    app()
