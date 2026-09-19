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


if __name__ == "__main__":  # pragma: no cover
    app()
