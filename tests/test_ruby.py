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
WS = (0.0, 0.0, 0.0, 0.0)         # 空白 / 换行（列边界）


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


def test_single_ruby_followed_by_kana_is_removed():
    """`学び舎やとして`：注音 `や` 后面直接跟正文假名 `と`。

    这是旧规则漏掉的形态——旧规则要求单个矮假名"左右都是汉字"，
    而这里右边是假名，于是 `舎や` 的注音一直留在正文里。
    现在靠**严阈值 0.56** 把促音排除在外，单个注音就可以放宽为"紧邻汉字即可"。
    """
    boxes = [BASE, BASE, BASE, RUBY, BASE, BASE, BASE]  # 学 び 舎 や と し て
    assert run("学び舎やとして", boxes) == "学び舎として"


def test_tsu_kept_even_when_adjacent_to_kanji():
    """`却って`：促音比值 0.61 落在严阈值 0.56 之外 → 即使紧邻汉字也保留。"""
    assert run("却って", [BASE, TSU, BASE]) == "却って"


def test_single_ruby_separated_by_column_break_is_removed():
    """真实竖排实况：注音与基字之间**正好夹着一个列边界**。

    `舎` `\\r\\n` `や` `\\r\\n` `と` —— 直接看 `raw[i-1]` 只会看到换行符，
    于是"紧邻汉字"永远不成立。这正是实测漏掉 `学び舎やとして` 的原因。
    """
    text = "舎\r\nや\r\nと"
    boxes = [BASE, WS, WS, RUBY, WS, WS, BASE]
    assert len(boxes) == len(text)
    out, _ = strip_inline_ruby(text, boxes, H)
    assert "や" not in out
    assert out.startswith("舎") and out.endswith("と")


def test_ruby_run_across_column_break_is_removed():
    """`難` `\\r\\n` `が` `\\r\\n` `た` `\\r\\n` `い`：跨列边界的成串注音一起删。"""
    text = "難\r\nが\r\nた\r\nい"
    boxes = [BASE, WS, WS, RUBY, WS, WS, RUBY, WS, WS, BASE]
    assert len(boxes) == len(text)
    out, _ = strip_inline_ruby(text, boxes, H)
    assert "が" not in out and "た" not in out
    assert out.startswith("難") and out.endswith("い")


def test_tsu_across_column_break_is_kept():
    """跨列边界的促音（比值 0.61 ≥ 严阈值）不能被误删。"""
    text = "だ\r\nっ\r\nた"
    boxes = [BASE, WS, WS, TSU, WS, WS, BASE]
    assert len(boxes) == len(text)
    out, _ = strip_inline_ruby(text, boxes, H)
    assert "っ" in out


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
