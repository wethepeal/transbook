"""transbook 命令行入口。

M0 阶段只提供两个自检命令；随里程碑推进逐步加入
`extract` / `preview` / `translate` / `export-review` / `apply-review` / `render` / `status` / `retry`。
"""

from __future__ import annotations

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


if __name__ == "__main__":  # pragma: no cover
    app()
