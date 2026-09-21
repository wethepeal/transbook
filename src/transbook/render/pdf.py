"""IR + 译文 → PDF（Typst）。

引擎选型来自 M0 实测（`docs/m0-report.md` §4）：**WeasyPrint 在本机 Windows 因缺 GTK/Pango 直接失败**，
而 Typst 自带编译器、零外部依赖，1.23 s 出 A5 文档，且中文禁则/缩进/页码均经肉眼核验通过。

排版要点（对应你的 Q20：A5 单栏 + 思源宋体正文 / 黑体标题）：
* A5 纸型、2em 首行缩进、1.85 倍行距、两端对齐
* 标题用 `Noto Sans SC`（黑体），正文用 `Noto Serif SC`（宋体）——**本机已装**
* 每章另起一页、自动目录（`outline`）、页脚居中页码
* 双语模式：原文以灰色小字置于译文上方
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from transbook.ir import DocumentIR
from transbook.render.naming import output_stem

# Typst 标记里的特殊字符，需反斜杠转义
_SPECIAL = "\\#$*_`@<>[]~"
_TRANS = str.maketrans({c: "\\" + c for c in _SPECIAL})
_LINE_START = re.compile(r"^([-+/=])", re.MULTILINE)


def esc(text: str) -> str:
    """转义 Typst 标记特殊字符（含行首的列表/标题记号）。"""
    out = (text or "").translate(_TRANS)
    return _LINE_START.sub(r"\\\1", out)


PREAMBLE = """\
// 由 transbook 生成 —— 请勿手改（改 IR 或译文后重新渲染）
#set page(
  paper: "a5",
  margin: (x: 16mm, y: 18mm),
  numbering: "1",
  number-align: center,
)
#set text(font: ("Noto Serif SC", "SimSun"), size: 10.5pt, lang: "zh")
#set par(justify: true, first-line-indent: 2em, leading: 1.85em, spacing: 0.85em)
#show heading: set text(font: ("Noto Sans SC", "Microsoft YaHei"))
#show heading.where(level: 1): it => {
  pagebreak(weak: true)
  block(it)
}
#set figure(gap: 0.6em)
"""


@dataclass
class PdfResult:
    typ_path: Path
    pdf_path: Path | None
    chapters: int
    paragraphs: int
    images: int
    error: str = ""
    tables: int = 0
    footnotes: int = 0


def _image_ref(path: str | None) -> str:
    """图片统一从 assets/<basename> 引用（与 assets 目录结构一致）。"""
    import posixpath

    return f"assets/{posixpath.basename(path or '')}"


def build_typst(ir: DocumentIR, translations: dict[str, str], *, mode: str = "zh",
                title_page: bool = True, toc: bool = True) -> tuple[str, int, int, int]:
    """生成 Typst 源码。返回 (源码, 章节数, 段落数, 图片数)。"""
    lines: list[str] = [PREAMBLE]
    if title_page:
        lines += [
            "#align(center + horizon)[",
            f"  #text(size: 22pt, weight: \"bold\")[{esc(ir.doc.title or '未命名')}]",
            "  #v(0.8em)",
            f"  #text(size: 12pt)[{esc(ir.doc.author or '')}]",
            "]",
            "#pagebreak()",
        ]
    if toc:
        lines += ["#outline(title: [目录], indent: 1.2em, depth: 2)", "#pagebreak()"]

    chapters = paragraphs = images = 0
    started = False
    for b in ir.blocks:
        if b.type == "heading":
            chapters += 1
            started = True
            lines += ["", f"{'=' * min(b.level or 1, 3)} {esc(b.text)}", ""]
            continue
        if b.type == "image":
            images += 1
            lines += [
                "",
                f"#figure(image(\"{_image_ref(b.path)}\", width: 88%), "
                f"caption: [{esc(b.caption or '')}])",
                "",
            ]
            continue
        if b.type == "table":
            lines += _typst_table(b, translations, mode)
            continue
        if b.type == "footnote":
            tgt = (translations.get(b.id) or "").strip()
            if mode == "zh":
                if not tgt:
                    continue
                lines += [f"#text(size: 0.84em, fill: luma(80))[{esc(tgt)}]", ""]
            else:
                lines += [
                    f"#text(size: 0.8em, fill: luma(120))[{esc(b.text)}]",
                    "",
                    (f"#text(size: 0.84em, fill: luma(80))[{esc(tgt)}]" if tgt
                     else "#text(fill: luma(150))[（未译）]"),
                    "",
                ]
            continue
        if b.type != "paragraph":
            continue
        tgt = (translations.get(b.id) or "").strip()
        if mode == "zh":
            if not tgt:
                continue
            paragraphs += 1
            lines += [esc(tgt), ""]
        else:  # bilingual
            paragraphs += 1
            lines += [
                f"#text(size: 0.86em, fill: luma(120))[{esc(b.text)}]",
                "",
                esc(tgt) if tgt else "#text(fill: luma(150))[（未译）]",
                "",
            ]
    if not started:
        lines += ["#text(fill: luma(150))[（本书没有标题块）]"]
    return "\n".join(lines), chapters, paragraphs, images


def _typst_table(b, translations: dict[str, str], mode: str) -> list[str]:
    """表格 → Typst `table()`。单元格逐格取译文（unit id = `{block_id}:r{r}c{c}`）。"""
    ncol = max((len(r) for r in b.rows), default=0)
    if ncol == 0:
        return []
    cells: list[str] = []
    for r, row in enumerate(b.rows):
        for c, cell in enumerate(row):
            tgt = (translations.get(f"{b.id}:r{r}c{c}") or "").strip()
            if mode == "zh":
                txt = tgt or cell  # 缺译保留原文，避免表格错位
            else:
                txt = f"{cell} / {tgt}" if tgt else f"{cell} / （未译）"
            rs, cs = 1, 1
            if r < len(b.cell_spans) and c < len(b.cell_spans[r]):
                rs, cs = b.cell_spans[r][c]
            opts = [f"{k}: {v}" for k, v in (("rowspan", rs), ("colspan", cs)) if v > 1]
            cells.append(f"table.cell({', '.join(opts)})[{esc(txt)}]" if opts
                         else f"[{esc(txt)}]")
    out = ["", f"#table(columns: {ncol}, stroke: 0.4pt, inset: 5pt,"]
    if b.caption:
        out.append(f"  caption: [{esc(b.caption)}],")
    out.append("  " + ", ".join(cells) + ",")
    out.append(")")
    out.append("")
    return out


def _count_extras(ir: DocumentIR) -> tuple[int, int]:
    """统计表格 / 脚注块数（`build_typst` 的返回值保持不变，故单独数一遍）。"""
    t = sum(1 for b in ir.blocks if b.type == "table")
    f = sum(1 for b in ir.blocks if b.type == "footnote")
    return t, f


def write_typst(work_dir: str | Path, ir: DocumentIR, translations: dict[str, str], *,
                mode: str = "zh", name: str | None = None) -> PdfResult:
    """把 Typst 源码写到工作目录（图片按相对路径 `assets/` 引用）。"""
    work = Path(work_dir)
    src, chapters, paragraphs, images = build_typst(ir, translations, mode=mode)
    typ_path = work / (name or f"{output_stem(ir)}.{mode}.typ")
    typ_path.write_text(src, encoding="utf-8")
    tables, footnotes = _count_extras(ir)
    return PdfResult(typ_path=typ_path, pdf_path=None, chapters=chapters,
                     paragraphs=paragraphs, images=images, tables=tables,
                     footnotes=footnotes)


def compile_pdf(typ_path: str | Path, pdf_path: str | Path | None = None) -> PdfResult:
    """用 Typst 编译成 PDF。`typst` 未安装时返回带 error 的结果，不抛异常。"""
    src = Path(typ_path)
    out = Path(pdf_path) if pdf_path else src.with_suffix(".pdf")
    try:
        import typst  # 延迟导入：未安装时也能只生成 .typ 供人工检查
    except ImportError:
        return PdfResult(src, None, 0, 0, 0, error="未安装 typst（uv add typst）")
    try:
        typst.compile(str(src), output=str(out))
    except Exception as exc:  # noqa: BLE001 - 编译错误需回传给用户看
        return PdfResult(src, None, 0, 0, 0, error=f"{type(exc).__name__}: {exc}"[:400])
    return PdfResult(src, out, 0, 0, 0)


def render_pdf(work_dir: str | Path, ir: DocumentIR, translations: dict[str, str], *,
               mode: str = "zh", name: str | None = None) -> PdfResult:
    """一步到位：写 .typ 并编译成 PDF。"""
    res = write_typst(work_dir, ir, translations, mode=mode, name=name)
    compiled = compile_pdf(res.typ_path)
    compiled.chapters, compiled.paragraphs, compiled.images = res.chapters, res.paragraphs, res.images
    compiled.tables, compiled.footnotes = res.tables, res.footnotes
    return compiled


def available() -> bool:
    """Typst 是否可用。"""
    try:
        import typst  # noqa: F401
    except ImportError:
        return False
    return shutil.which("x") is not None or True
