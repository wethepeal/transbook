"""M2：PDF 内联振假名（ruby）剥离。

PDF 的注音没有 `<rt>` 标记，只能靠**字号**认（实测正文 h≈12.2、注音 h≈6.1）。
难点是同一页里标点（`、。` h≈4.2）和促音（`っ` h≈7.4）也很矮——
所以判据是"矮 + 是假名 + 成串"，而不是单纯看高度。
"""

from __future__ import annotations

from transbook.ingest.pdf import strip_inline_ruby

H = 12.0
BASE = (0.0, 0.0, 1.0, 12.0)      # 正文：高 12
RUBY = (0.0, 0.0, 1.0, 6.0)       # 注音：高 6（约一半）
PUNCT = (0.0, 0.0, 1.0, 4.0)      # 标点：高 4，更矮但不是假名
TSU = (0.0, 0.0, 1.0, 7.4)        # 促音 っ：高 7.4


def run(text: str, boxes: list) -> str:
    out, _ = strip_inline_ruby(text, boxes, H)
    return out


def test_multi_char_ruby_removed():
    """`難がたい` → `難`：连续 2 个矮假名判为注音。"""
    assert run("難がたい", [BASE, RUBY, RUBY, BASE]) == "難い"


def test_long_ruby_run_removed():
    assert run("佇たたずむ", [BASE, RUBY, RUBY, RUBY, BASE]) == "佇む"


def test_single_ruby_between_kanji_removed():
    """`手て強`：单个矮假名左右都是汉字 → 是注音。"""
    assert run("手て強ごわい", [BASE, RUBY, BASE, RUBY, RUBY, RUBY]) == "手強"


def test_tsu_between_kana_is_kept():
    """`だった` 的促音：左边是假名 → 绝不能删（这是真字，不是注音）。"""
    assert run("だった", [BASE, TSU, BASE]) == "だった"


def test_tsu_after_kanji_before_kana_is_kept():
    """`却って`：左汉字右假名 → 保留（规则要求左右**都**是汉字）。"""
    assert run("却って", [BASE, TSU, BASE]) == "却って"


def test_small_punctuation_is_kept():
    """标点比注音还矮，但不是假名 → 绝不删。"""
    assert run("文章、続き。", [BASE, BASE, PUNCT, BASE, BASE, PUNCT]) == "文章、続き。"


def test_ruby_adjacent_to_punctuation():
    """真实文本：`尽くし難がたい、` —— 只删注音，标点与正文都留。"""
    text = "尽くし難がたい、"
    boxes = [BASE, BASE, BASE, BASE, RUBY, RUBY, BASE, PUNCT]
    assert len(boxes) == len(text)
    assert run(text, boxes) == "尽くし難い、"


def test_boxes_stay_in_sync():
    """返回的 boxes 必须与文本一一对应，否则后面按索引取字符框会全错位。"""
    text = "難がたい"
    boxes = [BASE, RUBY, RUBY, BASE]
    out, out_boxes = strip_inline_ruby(text, boxes, H)
    assert out == "難い"
    assert len(out_boxes) == len(out) == 2
    assert out_boxes[0] == BASE and out_boxes[1] == BASE


def test_length_mismatch_is_left_alone():
    text = "難がたい"
    assert strip_inline_ruby(text, [BASE, RUBY], H) == (text, [BASE, RUBY])


def test_empty_input():
    assert strip_inline_ruby("", [], H) == ("", [])
    assert strip_inline_ruby("あ", [BASE], 0) == ("あ", [BASE])


def test_katakana_small_char_is_kept():
    """`シェット` 的 ェ：小而合法的片假名，左右不是汉字 → 保留。"""
    assert run("シェット", [BASE, TSU, BASE, BASE]) == "シェット"
