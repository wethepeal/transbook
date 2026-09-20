"""M2 文本清洗与前后附页归类测试。

覆盖三条独立能力：
1. `join_wrapped` —— 换行拼接（竖排直连 / 英文跨行断词 / 横排补空格）；
2. `normalize_ligatures` —— PDF 连字还原；
3. `classify_matter` —— 封面 / 目次 / 奥付 / 广告等归类（判定串取自真实样书）。
"""

from __future__ import annotations

import pytest

from transbook.ingest.pdf import segments_to_paragraphs
from transbook.textutil import (
    classify_matter,
    clean_paragraph,
    clean_pdf_text,
    is_cjk,
    join_wrapped,
    normalize_ligatures,
    squeeze_cjk_spaces,
)


# ── 换行拼接 ────────────────────────────────────────────────────────
def test_vertical_glues_without_separator():
    """竖排：列边界不加任何字符——这是竖排书能还原出 3317 段的前提。"""
    assert join_wrapped("夢の終わり", "は sudden", vertical=True) == "夢の終わりは sudden"


def test_hyphen_break_joins_word():
    """英文跨行断词：连字符应被吃掉，且不补空格。"""
    assert join_wrapped("trans-", "lation") == "translation"
    assert join_wrapped("eﬃ-", "cient") == "eﬃcient"


def test_hyphen_variants():
    """U+2010/U+2011 与软连字符同样是行尾断词写法（PDF 常见）。"""
    assert join_wrapped("trans\u2010", "lation") == "translation"
    assert join_wrapped("trans\u2011", "lation") == "translation"
    assert join_wrapped("trans\u00ad", "lation") == "translation"


def test_hyphen_before_uppercase_is_kept():
    """`US-` + `Based` 是真实连字符，不能吃掉。"""
    assert join_wrapped("US-", "Based") == "US- Based"


def test_latin_wrap_gets_space():
    """英文正常换行必须补空格，否则会粘成一个词。"""
    assert join_wrapped("the quick", "brown fox") == "the quick brown fox"


def test_cjk_wrap_gets_no_space():
    """日文横排换行不补空格（`日本` + `語` → `日本語`）。"""
    assert join_wrapped("日本語", "を学ぶ") == "日本語を学ぶ"
    assert join_wrapped("中文", "翻译") == "中文翻译"


def test_empty_sides_are_identity():
    assert join_wrapped("", "abc") == "abc"
    assert join_wrapped("abc", "") == "abc"


def test_is_cjk_boundaries():
    assert is_cjk("漢") and is_cjk("あ") and is_cjk("ア") and is_cjk("、") and is_cjk("！")
    assert not is_cjk("A") and not is_cjk("1") and not is_cjk(" ") and not is_cjk("")


# ── 连字 ────────────────────────────────────────────────────────────
def test_ligatures_expanded():
    assert normalize_ligatures("e\ufb03cient \ufb02ow \ufb01nal a\ufb00ord") == \
        "efficient flow final afford"
    assert normalize_ligatures("\ufb05reet") == "street"
    assert normalize_ligatures("\u017fong") == "song"


def test_ligature_noop_keeps_japanese_punctuation():
    """关键：**不能**用 NFKC 整体规范化，否则日文全角标点会被压成半角。"""
    ja = "第一章『決戦』！そう——（ここ）"
    assert normalize_ligatures(ja) == ja
    assert clean_pdf_text(ja) == ja


def test_clean_pdf_text_keeps_trailing_soft_hyphen():
    """软连字符必须留到拼接阶段，否则跨行断词会被拆成两个词。"""
    assert clean_pdf_text("trans\u00ad") == "trans\u00ad"
    assert clean_paragraph("trans\u00ad lation") == "trans lation"


# ── CJK 之间的空格压缩 ──────────────────────────────────────────────
def test_squeeze_space_between_cjk():
    """PDF 常把破折号拆成独立文本段，抽出来多一个空格；日文本来不加空格。"""
    assert squeeze_cjk_spaces("「── 星が悪かったんだよ」") == "「──星が悪かったんだよ」"
    assert squeeze_cjk_spaces("愛 でないと") == "愛でないと"
    assert squeeze_cjk_spaces("静けさを 愛でないと") == "静けさを愛でないと"


def test_squeeze_keeps_space_around_latin():
    """拉丁与 CJK 之间的空格是排版需要，必须保留。"""
    assert squeeze_cjk_spaces("Web サイト") == "Web サイト"
    assert squeeze_cjk_spaces("第 3 章") == "第 3 章"
    assert squeeze_cjk_spaces("hello world") == "hello world"


def test_squeeze_noop_on_clean_text():
    assert squeeze_cjk_spaces("日本語のテキスト。") == "日本語のテキスト。"
    assert squeeze_cjk_spaces("") == ""


# ── 横排段落还原 ────────────────────────────────────────────────────
def test_segments_to_paragraphs_horizontal_dehyphenates():
    """横排：dy≈0 是换行（拼接），dy≈1 字是字下げ（新段）。"""
    h = 10.0
    segs = [("Attention is all you", 0.0), ("need. It is e\ufb03-", 0.0),
            ("cient and simple.", 0.0), ("Second paragraph starts.", 10.0)]
    paras = segments_to_paragraphs(segs, h, vertical=False)
    assert paras == ["Attention is all you need. It is efficient and simple.",
                     "Second paragraph starts."]


def test_segments_to_paragraphs_vertical_unchanged():
    """竖排默认行为不能被 M2 改动破坏。"""
    h = 10.0
    segs = [("夢の終わり", 0.0), ("は sudden", 0.0), ("次の段落", 10.0)]
    assert segments_to_paragraphs(segs, h, vertical=True) == ["夢の終わりは sudden", "次の段落"]
    assert segments_to_paragraphs(segs, h) == ["夢の終わりは sudden", "次の段落"]


# ── 前后附页归类 ────────────────────────────────────────────────────
@pytest.mark.parametrize("path,title,want", [
    # 真实样书 Re:Zero 44 的 spine 文件名
    ("xhtml/p-cover.xhtml", "表紙", "cover"),
    ("xhtml/p-fmatter-001.xhtml", "本編", "front"),
    ("xhtml/p-toc-001.xhtml", "CONTENTS", "toc"),
    ("xhtml/p-002.xhtml", "プロローグ 『運命の夜』", "main"),
    ("xhtml/p-011.xhtml", "", "main"),
    ("xhtml/p-allcover-001.xhtml", "", "back"),
    ("xhtml/p-colophon.xhtml", "奥付", "colophon"),
    ("xhtml/p-colophon2.xhtml", "", "colophon"),
    ("xhtml/p-bookwalker.xhtml", "", "promo"),
    ("OEBPS/text/afterword.xhtml", "あとがき", "afterword"),
    ("OEBPS/text/chapter01.xhtml", "第一章", "main"),
    ("OEBPS/text/titlepage.xhtml", "", "front"),
])
def test_classify_matter_by_path(path, title, want):
    assert classify_matter(path, title) == want


def test_allcover_not_mistaken_for_cover():
    """`allcover`（裏表紙）里含 `cover` 子串，顺序错了就会被误判成封面。"""
    assert classify_matter("xhtml/p-allcover-001.xhtml", "表紙") == "back"


@pytest.mark.parametrize("title,want", [
    ("目次", "toc"), ("CONTENTS", "toc"), ("表紙", "cover"),
    ("奥付", "colophon"), ("あとがき", "afterword"), ("解説", "afterword"),
    ("第一章 『光の萌し』", "main"), ("幕間 『──どうして？』", "main"),
])
def test_classify_matter_by_title_only(title, want):
    """PDF 没有文件名线索，只能靠书签标题。"""
    assert classify_matter("", title) == want


def test_classify_matter_unknown_is_main():
    assert classify_matter("", "") == "main"
    assert classify_matter("xhtml/p-999.xhtml", "第七章") == "main"
