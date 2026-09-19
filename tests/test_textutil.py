"""文本清洗测试：注音剥离、空白归一化。"""

from __future__ import annotations

from lxml import html as LH

from transbook.textutil import count_rt, is_blank_block, normalize_ws, text_without_rt


def test_strip_rt_per_char():
    """逐字注音：<ruby>幕<rt>まく</rt>間<rt>あい</rt></ruby> → 幕間"""
    el = LH.fromstring("<p>第一章 『<ruby>幕<rt>まく</rt>間<rt>あい</rt></ruby>』</p>")
    assert text_without_rt(el) == "第一章 『幕間』"


def test_strip_rt_simple():
    el = LH.fromstring("<p><ruby>漢字<rt>かんじ</rt></ruby>を含みます。</p>")
    assert text_without_rt(el) == "漢字を含みます。"


def test_count_rt():
    el = LH.fromstring("<p><ruby>幕<rt>まく</rt>間<rt>あい</rt></ruby></p>")
    assert count_rt(el) == 2


def test_normalize_ws_fullwidth_space():
    assert normalize_ws("　あ　い\t\nう　") == "あ い う"


def test_blank_block_detection():
    assert is_blank_block("")
    assert is_blank_block("　 \n")
    assert not is_blank_block("本文")
