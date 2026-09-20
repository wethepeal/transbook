"""M3：引擎对比。

对比必须是**可判定的**：成本、耗时、以及复用 `tp qa` 口径的质量指标。
主观"哪个翻得好"不进来——那要靠人眼看逐条对照。
"""

from __future__ import annotations

import pytest

from transbook.quality.compare import _judge, compare, run_engine
from transbook.translate import BookContext
from transbook.translate.base import SegmentIn, SegmentOut, TranslationProvider, Usage
from transbook.translate.fake import FakeProvider

SRC = [
    "第一章 『氷上決戦』",
    "レムは氷の双剣を構え、アルデバランに向かって跳んだ。",
    "「────」",
    "The quick brown fox jumps over the lazy dog and keeps running.",
]


def items() -> list[SegmentIn]:
    return [SegmentIn(f"s{i}", t) for i, t in enumerate(SRC)]


# ── 判定 ────────────────────────────────────────────────────────────
def test_judge_flags_untranslated_only_with_kana():
    assert "untranslated" in _judge(SRC[1], SRC[1], {})
    assert "untranslated" not in _judge("「────」", "「────」", {})


def test_judge_flags_kana_left():
    assert "kana_left" in _judge(SRC[1], "蕾姆残留ひらがな。", {})
    assert "katakana_left" in _judge(SRC[1], "蕾姆用レム这个写法。", {})


def test_judge_flags_term_missing():
    assert "term_missing" in _judge(SRC[1], "她跳了过去。", {"レム": "蕾姆"})
    assert "term_missing" not in _judge(SRC[1], "蕾姆跳了过去。", {"レム": "蕾姆"})


def test_judge_flags_length_anomaly():
    assert "length_anomaly" in _judge(SRC[3], "短", {})


def test_judge_flags_empty():
    assert _judge(SRC[1], "   ", {}) == ["empty"]


def test_judge_flags_traditional_output():
    """实测本地 Qwen3-8B 整段输出繁体（`一章『氷上決戰』`）。

    这类问题**不含假名**，原来的"假名残留"检查完全抓不到。
    """
    assert "traditional" in _judge(SRC[0], "第一章『氷上決戰』", {})
    assert "traditional" in _judge(SRC[1], "那是他自認應該承擔的角色與使命。", {})
    assert "traditional" not in _judge(SRC[1], "雷姆自认，那是她应当承担的角色。", {})
    # 单个繁体字可能是刻意保留的人名用字，不报
    assert "traditional" not in _judge(SRC[1], "她跳向那個方向。", {})


def test_no_false_positive_on_plain_simplified():
    """**简繁同形字**绝不能进 NON_SIMPLIFIED 集合。

    早期把 `姐妹暖黑` 放进去，在真实成品上报了 5 条假警。
    """
    from transbook.quality.qa import NON_SIMPLIFIED

    plain = ("她姐妹俩都很喜欢这温暖的黑夜，时间过得很快，"
             "学习和经历让人成长，车马鱼鸟龙风长东乐买卖钱银铁铜都在纸上。")
    assert not [c for c in plain if c in NON_SIMPLIFIED], \
        f"简繁同形字被误收：{[c for c in plain if c in NON_SIMPLIFIED]}"
    assert "traditional" not in _judge(SRC[1], plain, {})


# ── 跑引擎 ──────────────────────────────────────────────────────────
def test_run_engine_collects_translations_and_usage():
    prov = FakeProvider(prefix="译:")
    res = run_engine(prov, items(), BookContext(doc_id="d"), batch_items=2)
    assert len(res.translations) == len(SRC)
    assert res.seconds >= 0 and res.usage.calls == 2  # 4 段 / 每批 2 段
    assert res.translations["s1"] == "译:" + SRC[1]


def test_run_engine_retries_missing_ids():
    class Flaky(FakeProvider):
        def __init__(self):
            super().__init__()
            self.n = 0

        def translate(self, batch, ctx, *, strict=False):
            self.n += 1
            if self.n == 1:
                return ([SegmentOut(b.seg_id, error="缺号") for b in batch], Usage(calls=1))
            return super().translate(batch, ctx, strict=strict)

    res = run_engine(Flaky(), items(), BookContext(doc_id="d"), batch_items=4)
    assert all(res.translations.values()), "重试后应补齐"
    assert not res.error


def test_run_engine_records_error_without_crashing():
    class Boom(TranslationProvider):
        name = "boom"

        def translate(self, batch, ctx, *, strict=False):
            raise RuntimeError("端点连不上")

    res = run_engine(Boom(), items(), BookContext(doc_id="d"))
    assert res.error and "RuntimeError" in res.error
    assert all(v == "" for v in res.translations.values())


def test_run_engine_estimates_tokens_when_endpoint_silent():
    """本地端点常不返回用量，token 估算必须兜底，否则成本/速度表是空的。"""
    res = run_engine(FakeProvider(), items(), BookContext(doc_id="d"))
    assert res.usage.tokens_in > 0 and res.usage.tokens_out > 0


# ── 汇总 ────────────────────────────────────────────────────────────
def test_compare_runs_every_engine_and_keeps_samples():
    a, b = FakeProvider(prefix="A:"), FakeProvider(prefix="B:")
    b.name = "local"
    rep = compare(items(), [a, b], BookContext(doc_id="d"), sample=2, batch_items=4)
    assert [r.name for r in rep.results] == ["fake", "local"]
    assert len(rep.samples) == 2
    seg_id, src, outs = rep.samples[0]
    assert outs["fake"].startswith("A:") and outs["local"].startswith("B:")
    assert "引擎" in rep.table()


def test_compare_survives_one_engine_failing():
    """一个引擎挂掉不能让整张对比表拿不到。"""
    class Boom(TranslationProvider):
        name = "boom"

        def translate(self, batch, ctx, *, strict=False):
            raise RuntimeError("down")

    rep = compare(items(), [Boom(), FakeProvider()], BookContext(doc_id="d"), sample=1)
    assert rep.by_name("boom").error
    assert rep.by_name("fake").translations["s0"]


def test_local_tier_is_free():
    """本地端点只有电费：沿用 API 单价会把本地算成花钱，还会让 --max-cost 误停。"""
    from transbook.translate.deepseek import DeepSeekProvider

    local = DeepSeekProvider("k", model="qwen3-8b", price_tier="local")
    api = DeepSeekProvider("k", model="deepseek-flash", price_tier="idle")
    assert local.estimate_cost(1_000_000, 1_000_000) == 0.0
    assert api.estimate_cost(1_000_000, 1_000_000) > 0.0


def test_per_million_chars_helper():
    """换算成"每百万源字符"便于跨规模比较。"""
    from transbook.quality.compare import EngineResult
    from transbook.translate.base import Usage

    r = EngineResult(name="x", translations={"a": "x" * 100}, usage=Usage(cost=0.5))
    assert r.per_million_chars() == pytest.approx(5000.0)
    assert EngineResult(name="y").per_million_chars() == 0.0
