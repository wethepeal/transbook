"""PDF 渲染测试：Typst 转义、源码结构、**编译产物文字级验证**。"""

from __future__ import annotations

from pathlib import Path

import pytest

from transbook.ir import Block, DocMeta, DocumentIR
from transbook.render import build_typst, render_pdf
from transbook.render.pdf import esc


def make_ir(with_image: bool = True) -> DocumentIR:
    blocks = [
        Block(id="b000001", type="heading", level=1, text="第一章 『決戦』"),
        Block(id="b000002", type="paragraph", text="原文一段。"),
        Block(id="b000003", type="paragraph", text="原文二段。"),
    ]
    if with_image:
        blocks.append(Block(id="b000004", type="image", path="images/pic.png", caption="插图"))
    return DocumentIR(doc=DocMeta(id="doc", title="测试书", author="作者", source_lang="ja",
                                  origin="epub"), blocks=blocks)


TR = {"b000002": "译文一段。", "b000003": "译文二段。"}


# ── 转义 ────────────────────────────────────────────────────────────
def test_esc_special_chars():
    assert esc("a#b$c*d_e") == "a\\#b\\$c\\*d\\_e"
    assert esc("[x]") == "\\[x\\]"
    assert esc("正<常>") == "正\\<常\\>"


def test_esc_line_start_markers():
    assert esc("- 列表项").startswith("\\-")
    assert esc("+ 编号").startswith("\\+")
    assert esc("#标签").startswith("\\#")


def test_esc_keeps_plain_cjk():
    assert esc("レムとアル、氷上決戦。") == "レムとアル、氷上決戦。"


# ── 源码结构 ────────────────────────────────────────────────────────
def test_zh_mode_contains_only_translation():
    src, chapters, paras, images = build_typst(make_ir(), TR, mode="zh")
    assert "译文一段。" in src and "原文一段。" not in src
    assert chapters == 1 and paras == 2 and images == 1


def test_bilingual_contains_both():
    src, *_ = build_typst(make_ir(), TR, mode="bilingual")
    assert "原文一段。" in src and "译文一段。" in src
    assert "luma(120)" in src, "原文应以灰色小字呈现"


def test_heading_and_image_markup():
    src, *_ = build_typst(make_ir(), TR, mode="zh")
    assert "= 第一章 『決戦』" in src
    assert '#figure(image("assets/pic.png"' in src
    assert "caption: [插图]" in src


def test_preamble_has_a5_and_fonts():
    src, *_ = build_typst(make_ir(), TR, mode="zh")
    assert 'paper: "a5"' in src
    assert "Noto Serif SC" in src and "Noto Sans SC" in src
    assert "first-line-indent: 2em" in src
    assert "#outline(" in src


def test_missing_translation_in_zh_is_skipped():
    src, _, paras, _ = build_typst(make_ir(), {}, mode="zh")
    assert paras == 0 and "原文一段。" not in src


# ── 编译与文字级验证 ────────────────────────────────────────────────
def test_render_pdf_and_extract_text(tmp_path: Path):
    """编译出 PDF，并用 pypdfium2 取回文字——证明中文字形真的写进去了。"""
    pytest.importorskip("typst")
    pdfium = pytest.importorskip("pypdfium2")
    (tmp_path / "assets").mkdir()
    res = render_pdf(tmp_path, make_ir(with_image=False), TR, mode="zh")
    assert not res.error, res.error
    assert res.pdf_path and res.pdf_path.is_file()
    assert res.pdf_path.read_bytes()[:5] == b"%PDF-"

    doc = pdfium.PdfDocument(res.pdf_path)
    text = "".join(doc[i].get_textpage().get_text_range() for i in range(len(doc)))
    flat = "".join(text.split())
    assert "译文一段。" in flat and "译文二段。" in flat
    assert "第一章" in flat
    assert "原文一段。" not in flat, "纯中文版不应出现原文"


def test_render_pdf_bilingual_has_source(tmp_path: Path):
    pytest.importorskip("typst")
    pdfium = pytest.importorskip("pypdfium2")
    (tmp_path / "assets").mkdir()
    res = render_pdf(tmp_path, make_ir(with_image=False), TR, mode="bilingual")
    assert not res.error, res.error
    doc = pdfium.PdfDocument(res.pdf_path)
    flat = "".join("".join(doc[i].get_textpage().get_text_range().split()) for i in range(len(doc)))
    assert "原文一段。" in flat and "译文一段。" in flat


def test_write_typst_even_without_typst_installed(tmp_path: Path):
    """即使 typst 不可用，也应产出可人工检查的 .typ 源码。"""
    res = render_pdf(tmp_path, make_ir(), TR, mode="zh")
    assert res.typ_path.is_file()
    assert "译文一段。" in res.typ_path.read_text(encoding="utf-8")
