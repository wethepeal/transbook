"""PDF 抽取测试：竖排判定、段落切分（纯函数）+ 用自造 PDF 做端到端验证。

竖排的真实样例无法用代码生成（Typst 不支持縦書き），因此：
* **几何判定与段落切分**用真实竖排书的实测数值做单元测试（数值取自 Re:Zero 竖排 PDF 第 19 页）；
* **端到端**用 Typst 自造一份横排 PDF，验证抽取链路本身可用。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from transbook.ingest import PdfIngestor, detect_vertical, segment_kind, segments_to_paragraphs
from transbook.ingest.pdf import _strip_title_prefix, char_height


# ── 竖排判定（真实竖排书的位移特征）──────────────────────────────────
def test_detect_vertical_true_for_tategaki():
    # 竖排：字符 x 基本不变、y 逐字递减（实测：x≈516 恒定，y 689→673→656…）
    samples = [(0.5, -16.2), (-0.5, -16.8), (4.7, -17.2), (-6.2, -17.8), (1.0, -15.9)]
    assert detect_vertical(samples) is True


def test_detect_vertical_false_for_horizontal():
    samples = [(12.4, 0.2), (11.8, -0.3), (13.0, 0.1), (12.2, 0.0)]
    assert detect_vertical(samples) is False


def test_detect_vertical_edge_cases():
    assert detect_vertical([]) is False
    assert detect_vertical([(0.0, 0.0), (0.0, 0.0)]) is False


# ── 字符高度 ────────────────────────────────────────────────────────
def test_char_height_median():
    boxes = [(0, 0, 10, 14), (0, 0, 10, 16), (0, 0, 10, 14), (0, 0, 10, 100)]
    assert char_height(boxes) == 16  # 排序 [14,14,16,100] → 中位偏上


# ── 段落切分（数值取自真实竖排书实测）────────────────────────────────
H = 14.2  # 实测字符高度


def test_segment_kind_real_values():
    assert segment_kind(0.0, H) == "cont"      # 续行：贴顶
    assert segment_kind(2.8, H) == "cont"      # 续行
    assert segment_kind(8.4, H) == "start"     # 「────」（引号字形矮，仍属段落起首）
    assert segment_kind(14.7, H) == "start"    # 新段落（下げ 1 字）
    assert segment_kind(15.0, H) == "start"
    assert segment_kind(78.0, H) == "inner"    # 振假名
    assert segment_kind(329.4, H) == "inner"   # 列中片段


def test_segments_to_paragraphs_real_page19():
    """复刻真实竖排书第 19 页的列段序列，检查段落还原。"""
    segs = [
        ("プロローグ『夢の終わり』", 14.7),   # 章节标题列 → 新段落
        ("──夢の城に帰り着いた", 0.0),        # 续行
        ("既知の光景、既知の芳香", 14.8),     # 新段落
        ("ぬく", 329.4),                      # 振假名 → 并入
        ("もり。", 345.1),                    # 续行 → 并入
        ("「──── 」", 8.4),                   # 新段落
        ("まさしく、文字通り", 15.0),         # 新段落
    ]
    paras = segments_to_paragraphs(segs, H)
    assert len(paras) == 4
    assert paras[0] == "プロローグ『夢の終わり』──夢の城に帰り着いた"
    assert paras[1] == "既知の光景、既知の芳香ぬくもり。"
    assert paras[2] == "「────」", "CJK 之间的空格是 PDF 抽取噪声，须压掉（EPUB 真值即无空格）"
    assert paras[3] == "まさしく、文字通り"


def test_segments_to_paragraphs_skips_blank():
    assert segments_to_paragraphs([("  ", 14.0), ("本文", 14.0)], H) == ["本文"]


def test_strip_title_prefix():
    assert _strip_title_prefix("プロローグ『夢の終わり』 ── 夢の城に", "プロローグ『夢の終わり』") \
        == "夢の城に"
    assert _strip_title_prefix("無関係な本文", "第一章") == "無関係な本文"


# ── 端到端：自造横排 PDF ────────────────────────────────────────────
def _pdf_text(path: Path) -> str:
    """用 pypdfium2 快速取回 PDF 文本，判断这份 PDF 到底渲染出了什么。"""
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(path)
    try:
        return "".join(page.get_textpage().get_text_range() for page in doc)
    finally:
        doc.close()


@pytest.fixture
def sample_pdf(tmp_path: Path) -> Path:
    """用 Typst 造一份两章的横排 PDF（含中文段落），供抽取链路验证。"""
    pytest.importorskip("typst")
    import typst

    src = tmp_path / "t.typ"
    src.write_text(
        '#set page(paper: "a5", margin: 18mm)\n'
        # 字体给一串回退。原来只写 "Noto Serif SC" 一个：CI 的英文 runner 上没装它，
        # Typst 会静默换成别的字体，字宽不同→断行不同→`get_text_range()` 与
        # `count_chars()` 的长度不再相等（曾把一处 `zip(strict=True)` 撑爆）。
        '#set text(font: ("Noto Serif SC", "Microsoft YaHei", "SimSun",'
        ' "Noto Sans CJK SC", "Arial Unicode MS"), size: 11pt, lang: "zh")\n'
        "= 第一章 测试章节\n\n"
        "这是第一段中文正文，用于验证 PDF 抽取链路能否正确还原段落文本。\n\n"
        "这是第二段中文正文，内容与上一段不同，便于区分。\n\n"
        "= 第二章 另一章\n\n"
        "第二章的正文段落，用来确认章节与段落都被正确抽取出来。\n",
        encoding="utf-8",
    )
    out = tmp_path / "t.pdf"
    typst.compile(str(src), output=str(out))
    # 一个中文字体都没有的环境会把汉字渲染成缺字框，抽出来自然没有中文。
    # 那是**环境渲染不出来**，不是抽取链路的缺陷，所以如实跳过而不是报红——
    # 否则 CI 会因为"runner 没装字体"长期变红，久而久之就没人看 CI 了。
    if "第一段" not in _pdf_text(out):
        pytest.skip("本机没有可用的中文字体，Typst 渲染不出可抽取的中文")
    return out


def test_pdf_ingest_horizontal(sample_pdf: Path):
    ir = PdfIngestor(sample_pdf, doc_id="t").extract()
    assert ir.doc.origin == "pdf"
    assert ir.doc.vertical is False, "横排 PDF 不应被判为竖排"
    texts = [b.text for b in ir.blocks if b.type == "paragraph"]
    joined = "".join(texts)
    assert "第一段中文正文" in joined
    assert "第二段中文正文" in joined
    assert "第二章的正文段落" in joined


def test_pdf_ingest_is_serialisable(sample_pdf: Path):
    ir = PdfIngestor(sample_pdf, doc_id="t").extract()
    assert ir.model_dump_json()  # 能序列化（IR 契约）
    assert all(b.id for b in ir.blocks)
    ids = [b.id for b in ir.blocks]
    assert len(ids) == len(set(ids)), "块 ID 必须唯一"
