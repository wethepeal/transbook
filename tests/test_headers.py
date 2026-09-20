"""M2：页眉/页脚/页码过滤。

两条独立判据，分别对付两类污染：

1. **书眉文本**（`find_margin_segments` + `confirm_running_heads`）：
   贴推进轴边缘 + 在该轴上独一无二 + 跨页复现。
   判据要这么严，是因为真实竖排书里 `「──── 」` 在 53 页（14.2%）出现、位置离散度为 0、
   且正处页面最右——纯"贴边+重复"会把它整批误删（它是每列列首的正文）。
2. **页码**（`find_page_number_rows`）：页码每页数字都不同，**无法按文本比对**，
   但位置固定，故按 **y 跨页聚类**。实测真实竖排书 45 页页码全在 y≈632，
   而 x 从 96 跳到 499（用"贴版心边缘"只命中 14 页，不够）。
"""

from __future__ import annotations

from transbook.ingest.pdf import (
    confirm_running_heads,
    find_margin_segments,
    find_page_number_rows,
    is_page_number,
)


def rows(*items: tuple) -> list[tuple[str, float, float, float]]:
    """测试用：`(文本, y)` 或 `(文本, y, x)` → `(文本, 阅读轴偏移, y, x)`。"""
    return [(it[0], 0.0, it[1], it[2] if len(it) > 2 else 0.0) for it in items]


def hrows(*items: tuple[str, float]) -> list[tuple[str, float, float, float]]:
    """横排：y 是推进轴；给每段一个独有的 y。"""
    return [(t, 0.0, y, 100.0) for t, y in items]


# ── 页码识别 ────────────────────────────────────────────────────────
def test_is_page_number():
    assert is_page_number("4") and is_page_number("12") and is_page_number("３６")
    assert is_page_number("iv") and is_page_number("XII")
    assert not is_page_number("第一章") and not is_page_number("4.")
    assert not is_page_number("") and not is_page_number("12345")


# ── 判据 1：书眉（推进轴边缘 + 独一无二）────────────────────────────
def test_footer_page_number_is_not_a_margin_candidate():
    """纯数字段不走书眉判据（由页码聚类处理），避免双重口径打架。"""
    body = [(f"line {i}", 300.0 - i * 12) for i in range(20)]
    page = hrows(*body) + [("4", 0.0, 42.0, 100.0)]
    assert find_margin_segments(page, vertical=False) == set()


def test_header_text_at_top_edge_is_candidate():
    """横排首行也贴页顶，但它与下一行间距正常 → 不是书眉。"""
    page = hrows(("First body line", 300.0), ("second", 288.0), ("third", 276.0))
    assert find_margin_segments(page, vertical=False) == set()
    # 真的悬空在版心之外（与最近一行差 27，行距中位数 12）→ 候选
    page = hrows(*[(f"line {i}", 300.0 - i * 12) for i in range(20)],
                 ("第三章　氷上決戦", 40.0))
    assert find_margin_segments(page, vertical=False) == {20}


def test_vertical_dash_line_is_kept():
    """真实竖排书：`「──── 」` 在 53 页出现且位于页面最右，但**与同列共享同一个 x**
    （距离 0）→ 绝不能被判成书眉。"""
    page = rows(("「──── 」", 706.0, 523.0), ("同列的下一段", 690.0, 523.0),
                ("左一列", 706.0, 496.0), ("再左一列", 706.0, 469.0))
    assert find_margin_segments(page, vertical=True) == set()


def test_vertical_running_head_is_candidate():
    """竖排书的书眉坐在推进轴（x）最外侧、且独占该 x → 候选。"""
    page = rows(("第三章　氷上決戦", 706.0, 60.0), ("本文の列", 706.0, 170.1),
                ("本文の列２", 706.0, 196.1), ("本文の列３", 706.0, 222.1))
    assert find_margin_segments(page, vertical=True) == {0}


def test_vertical_page_number_goes_to_the_number_rule():
    """竖排书页码即使独处版心之外，也**不走书眉判据**——避免两套口径打架。"""
    page = rows(("３", 631.8, 119.1), ("本文の列", 706.0, 170.1),
                ("本文の列２", 706.0, 196.1), ("本文の列３", 706.0, 222.1))
    assert find_margin_segments(page, vertical=True) == set()


def test_long_text_in_margin_is_kept():
    page = hrows(*[(f"line {i}", 300.0 - i * 12) for i in range(20)],
                 ("あ" * 60, 40.0))
    assert find_margin_segments(page, vertical=False) == set()


def test_tiny_page_is_ignored():
    assert find_margin_segments(hrows(("a", 100.0), ("b", 40.0)), vertical=False) == set()
    assert find_margin_segments([], vertical=False) == set()


# ── 判据 2：页码按 y 跨页聚类 ───────────────────────────────────────
def _page(cross_y: float, text: str = "3") -> tuple[list[tuple[str, float, float, float]], float]:
    body = [(f"line {i}", 0.0, 300.0 - i * 12, 100.0) for i in range(5)]
    body.append((text, 0.0, cross_y, 300.0))
    return body, 10.0


def test_page_numbers_cluster_by_y_across_pages():
    """页码数字每页都不同，但 y 一致 → 靠 y 聚类识别。"""
    pages = [_page(42.0, str(n)) for n in range(1, 11)]
    rows_hit = find_page_number_rows(pages, tol=8.0, min_pages=3)
    assert rows_hit == {round(42.0 / 8.0)}


def test_vertical_page_numbers_cluster_by_y_despite_x_varying():
    """真实竖排书实况：y 恒为 632，x 在 96~499 之间乱跳。"""
    pages = []
    xs = [119.1, 95.9, 206.9, 259.7, 499.0, 498.9, 300.0]
    for i, x in enumerate(xs):
        body = [("本文の列", 0.0, 706.0, 170.0 + i * 27)]
        body.append((str(i + 3), 76.0, 631.8, x))
        pages.append((body, 12.0))
    assert find_page_number_rows(pages, tol=8.0, min_pages=3) == {round(631.8 / 8.0)}


def test_single_stray_number_is_not_a_page_number_row():
    """只在一两页出现的贴边数字 → 证据不足，不当作页码。"""
    pages = [_page(42.0, str(n)) for n in range(1, 11)]
    pages.append(([("x", 0.0, 300.0, 100.0), ("7", 0.0, 55.0, 300.0)], 10.0))
    assert find_page_number_rows(pages, tol=8.0, min_pages=3) == {round(42.0 / 8.0)}


def test_page_number_threshold_scales_with_length():
    """长篇文档要求更多页命中，避免偶发数字被当成页码行。"""
    pages = [_page(42.0, str(n)) for n in range(1, 6)] + \
            [([("body", 0.0, 300.0, 100.0)], 10.0)] * 395
    assert find_page_number_rows(pages, tol=8.0, min_pages=3) == set(), "5/400 = 1.25% < 2%"
    pages = [_page(42.0, str(n)) for n in range(1, 20)] + \
            [([("body", 0.0, 300.0, 100.0)], 10.0)] * 381
    assert find_page_number_rows(pages, tol=8.0, min_pages=3) == {round(42.0 / 8.0)}


# ── 跨页确认：书眉文本 ──────────────────────────────────────────────
def test_repeated_running_head_removed():
    cands = [["第三章　氷上決戦"]] * 12 + [[]] * 8
    assert confirm_running_heads(cands, pages=20) == {"第三章　氷上決戦"}


def test_repeated_text_below_threshold_kept():
    cands = [["まえがき"]] * 2 + [[]] * 18
    assert confirm_running_heads(cands, pages=20) == set()


def test_numbers_are_ignored_by_running_head_rule():
    """数字交给页码聚类，不在书眉规则里重复处理。"""
    cands = [["1"], ["2"], ["3"], ["4"]]
    assert confirm_running_heads(cands, pages=4) == set()


def test_threshold_scales_with_document_length():
    assert confirm_running_heads([["書名"]] * 8 + [[]] * 392, pages=400) == set()
    assert confirm_running_heads([["書名"]] * 25 + [[]] * 375, pages=400) == {"書名"}
