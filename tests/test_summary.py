"""M3：滚动摘要。

设计要点：**从原文按章生成**（不是从译文），否则要先全译完才能摘要，成了鸡生蛋。
每章只跑一次，第 N 章的梗概在前 N-1 章的基础上增量生成。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from transbook.ir import Block, DocumentIR, DocMeta
from transbook.store import connect, import_ir
from transbook.store.db import put_summary, summary_before, summaries
from transbook.translate import BookContext
from transbook.translate.base import SegmentOut, Usage
from transbook.translate.fake import FakeProvider
from transbook.translate.runner import run
from transbook.translate.summary import (build_summary_messages, chapter_of_block,
                                         chapter_spans, generate_summaries,
                                         per_chapter_budget)


def make_ir() -> DocumentIR:
    blocks = [
        Block(id="b000000", type="paragraph", text="卷首语。"),          # 标题之前
        Block(id="b000001", type="heading", level=1, text="第一章"),
        Block(id="b000002", type="paragraph", text="第一章正文。"),
        Block(id="b000003", type="heading", level=1, text="第二章"),
        Block(id="b000004", type="paragraph", text="第二章正文。"),
        Block(id="b000005", type="heading", level=2, text="小节"),
        Block(id="b000006", type="paragraph", text="小节正文。"),
        Block(id="b000007", type="heading", level=1, text="第三章"),
        Block(id="b000008", type="paragraph", text="第三章正文。"),
    ]
    return DocumentIR(doc=DocMeta(id="doc", title="书", source_lang="ja", origin="epub"),
                      blocks=blocks)


# ── 分章 ────────────────────────────────────────────────────────────
def test_chapter_spans_split_at_top_level_heading():
    """切在**最上层**标题上；二级小标题不另起一章。标题块算作本章成员。"""
    spans = chapter_spans(make_ir())
    assert [s.title for s in spans] == ["（前言）", "第一章", "第二章", "第三章"]
    assert [len(s.blocks) for s in spans] == [1, 2, 4, 2]


def test_chapter_of_block_covers_headings_too():
    """标题块也是可翻译段，必须能查到所属章，否则注入会错章。"""
    m = chapter_of_block(make_ir())
    assert m["b000000"] == 0 and m["b000001"] == 1 and m["b000002"] == 1
    assert m["b000006"] == 2 and m["b000007"] == 3 and m["b000008"] == 3


def test_chapter_spans_empty_book():
    ir = DocumentIR(doc=DocMeta(id="d", title="t", origin="epub"), blocks=[])
    assert chapter_spans(ir) == []


def test_chapter_spans_skip_non_story_matter():
    """目录/奥付不含情节，写进前情纯属噪声。

    实测真实样书里 "CONTENTS" 与 "奥付" 都各自被当成了一章。
    """
    blocks = [
        Block(id="b1", type="heading", level=1, text="CONTENTS", matter="toc"),
        Block(id="b2", type="paragraph", text="第一章……", matter="toc"),
        Block(id="b3", type="heading", level=1, text="第一章", matter="main"),
        Block(id="b4", type="paragraph", text="正文。", matter="main"),
        Block(id="b5", type="heading", level=1, text="奥付", matter="colophon"),
        Block(id="b6", type="paragraph", text="发行所。", matter="colophon"),
    ]
    ir = DocumentIR(doc=DocMeta(id="d", title="t", origin="epub"), blocks=blocks)
    spans = chapter_spans(ir)
    assert [s.title for s in spans] == ["第一章"]
    assert [b.id for b in spans[0].blocks] == ["b3", "b4"]
    # 章号允许不连续：目录占了 1 号但被丢弃，正文仍是 2 号，取前情用 `<` 比较不受影响
    assert spans[0].index == 2


# ── 提示词 ──────────────────────────────────────────────────────────
def test_summary_prompt_is_per_chapter():
    """**单章**摘要：不把已有前情塞进去合并。

    实测"合并并压缩到 N 字"完全不可靠（要 800 字，十章后涨到 3093 字），
    所以改成每章独立短摘要 + 注入时取最近 K 章，增长确定性有界。
    """
    span = chapter_spans(make_ir())[1]
    msgs = build_summary_messages(span, source_lang="ja", per_chapter=200)
    user = msgs[1]["content"]
    assert "第一章" in user and "200 字" in user
    assert "已有前情" not in user, "单章摘要不应携带累积前情"
    assert "不超过 200 字" in msgs[0]["content"]


def test_per_chapter_budget_shrinks_with_window():
    assert per_chapter_budget(800, 4) == 200
    assert per_chapter_budget(800, 1) == 800
    assert per_chapter_budget(100, 10) == 80, "有下限兜底，避免窗口大时预算归零"


# ── 生成与断点续跑 ──────────────────────────────────────────────────
def test_generate_summaries_is_per_chapter(tmp_path):
    """每章一条**独立**摘要——不是累积，所以长度不会一章比一章长。"""
    conn = connect(tmp_path / "t.db")
    rep = generate_summaries(conn, FakeProvider(prefix=""), make_ir(),
                             BookContext(doc_id="doc"))
    got = summaries(conn, "doc")
    assert rep.generated == 4 and rep.skipped == 0
    assert got[1] == "[梗概截至第1章]"
    assert got[3] == "[梗概截至第3章]"
    conn.close()


def test_generation_cost_does_not_grow_with_chapter_count(tmp_path):
    """单章摘要的输入规模只看本章——累积式会随章数线性膨胀。"""
    conn = connect(tmp_path / "t.db")
    prov = FakeProvider()
    generate_summaries(conn, prov, make_ir(), BookContext(doc_id="doc"))
    # 每次 complete 都只处理一章，调用次数 == 章数
    assert len([c for c in prov.calls if str(c[0]).startswith("complete:")]) == 4
    conn.close()


def test_generate_summaries_prunes_stale_rows(tmp_path):
    """分章规则变了以后，旧行必须清掉。

    实测：把目录/奥付排除出分章范围后，上一轮留下的累积摘要（2393 字）
    仍留在库里，`summary_before` 照取不误，被当成"奥付那章的前情"注入。
    """
    conn = connect(tmp_path / "t.db")
    put_summary(conn, "doc", 99, summary="早就过期的摘要")
    rep = generate_summaries(conn, FakeProvider(), make_ir(), BookContext(doc_id="doc"))
    assert rep.pruned == 1
    assert 99 not in summaries(conn, "doc")
    conn.close()


def test_generate_summaries_resumes(tmp_path):
    """已生成的章默认沿用——省钱、可断点续跑。"""
    conn = connect(tmp_path / "t.db")
    generate_summaries(conn, FakeProvider(), make_ir(), BookContext(doc_id="doc"))
    rep = generate_summaries(conn, FakeProvider(), make_ir(), BookContext(doc_id="doc"))
    assert rep.generated == 0 and rep.skipped == 4
    conn.close()


def test_generate_summaries_force(tmp_path):
    conn = connect(tmp_path / "t.db")
    generate_summaries(conn, FakeProvider(), make_ir(), BookContext(doc_id="doc"))
    rep = generate_summaries(conn, FakeProvider(), make_ir(), BookContext(doc_id="doc"),
                             force=True)
    assert rep.generated == 4 and rep.skipped == 0
    conn.close()


def test_summary_before_takes_a_window(tmp_path):
    """取**最近 window 章**且严格早于本章；不够就只给有的。"""
    conn = connect(tmp_path / "t.db")
    for i in (1, 2, 3, 4):
        put_summary(conn, "doc", i, summary=f"第{i}章梗概", title=f"第{i}章")
    got = summary_before(conn, "doc", 5, window=2)
    assert "第3章梗概" in got and "第4章梗概" in got
    assert "第2章梗概" not in got, "超出窗口的章不该出现"
    assert got.startswith("前情提要：")
    # 严格早于本章：第 1 章之前没有任何前情
    assert summary_before(conn, "doc", 1) == ""
    assert summary_before(conn, "doc", 2, window=4).count("梗概") == 1
    conn.close()


# ── 翻译时注入 ──────────────────────────────────────────────────────
class RecordingProvider(FakeProvider):
    """记录每批看到的前情，用于验证"按章注入"。"""

    def __init__(self) -> None:
        super().__init__()
        self.seen: list[str] = []

    def translate(self, items, ctx, *, strict=False):
        self.seen.append(ctx.rolling_summary)
        return super().translate(items, ctx, strict=strict)


def _seeded(tmp_path: Path):
    conn = connect(tmp_path / "t.db")
    ir = make_ir()
    import_ir(conn, ir)
    return conn, ir


def test_runner_injects_previous_chapter_summary(tmp_path):
    conn, ir = _seeded(tmp_path)
    put_summary(conn, "doc", 1, summary="【第一章梗概】")
    put_summary(conn, "doc", 2, summary="【第一二章梗概】")
    prov = RecordingProvider()
    # 每批一段，才能观察到逐章变化。段落顺序：卷首语 / 第一章标题 / 第一章正文 /
    # 第二章标题 / 第二章正文 / 小节标题 / 小节正文 / 第三章标题 / 第三章正文
    run(conn, prov, BookContext(doc_id="doc"), batch_items=1,
        ir=ir, rolling_summary=True, summary_window=2)
    # 前情取**严格早于本章**的最近 2 章：第 0/1 章看不到，第 2 章看到第 1 章，
    # 第 3 章看到第 1~2 章
    assert prov.seen[:3] == ["", "", ""], "卷首语与第一章之前没有前情"
    assert all("【第一章梗概】" in s and "【第一二章梗概】" not in s
               for s in prov.seen[3:7]), "第二章（含小节）只应看到第一章前情"
    assert all("【第一二章梗概】" in s for s in prov.seen[7:]), \
        "第三章应看到截至第二章的前情"
    conn.close()


def test_runner_without_flag_sends_nothing(tmp_path):
    conn, ir = _seeded(tmp_path)
    put_summary(conn, "doc", 1, summary="【第一章梗概】")
    prov = RecordingProvider()
    run(conn, prov, BookContext(doc_id="doc"), batch_items=1, ir=ir)
    assert set(prov.seen) == {""}
    conn.close()


def test_rolling_summary_requires_ir(tmp_path):
    conn, _ir = _seeded(tmp_path)
    with pytest.raises(ValueError):
        run(conn, FakeProvider(), BookContext(doc_id="doc"), rolling_summary=True)
    conn.close()


def test_dry_run_counts_summary_overhead(tmp_path):
    """前情是每批都重复注入的固定前缀，干跑必须把它算进预估。"""
    conn, ir = _seeded(tmp_path)
    put_summary(conn, "doc", 1, summary="【很长的前情】" * 40)
    from transbook.translate.deepseek import DeepSeekProvider

    prov = DeepSeekProvider("k", model="deepseek-flash", price_tier="idle")
    args = dict(batch_items=1, ir=ir, rolling_summary=True)
    with_sum = run(conn, prov, BookContext(doc_id="doc"), dry_run=True, **args)
    without = run(conn, prov, BookContext(doc_id="doc"), dry_run=True, batch_items=1)
    assert with_sum.est_tokens_in > without.est_tokens_in
    conn.close()
