"""翻译流水线测试：批处理、Fake 全链路、缺号重试、成本护栏、干跑、JSON 解析。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from transbook.ir import Block, DocumentIR, DocMeta
from transbook.store import connect, import_ir, pending, stats
from transbook.translate import (
    BookContext,
    DeepSeekProvider,
    FakeProvider,
    SegmentIn,
    TranslationFormatError,
    make_batches,
    parse_translations,
    run,
)


# ── 批处理 ──────────────────────────────────────────────────────────
def test_batches_cover_all_items_in_order():
    items = [SegmentIn(f"s{i}", "あ" * 100) for i in range(10)]
    batches = make_batches(items, max_chars=250, max_items=100)
    flat = [i.seg_id for b in batches for i in b]
    assert flat == [i.seg_id for i in items]
    assert all(sum(len(x.text) for x in b) <= 250 or len(b) == 1 for b in batches)


def test_batches_respect_item_cap():
    items = [SegmentIn(f"s{i}", "x") for i in range(10)]
    batches = make_batches(items, max_chars=10_000, max_items=3)
    assert [len(b) for b in batches] == [3, 3, 3, 1]


def test_batches_handle_oversized_single_item():
    batches = make_batches([SegmentIn("big", "あ" * 5000)], max_chars=100)
    assert len(batches) == 1 and len(batches[0]) == 1


# ── JSON 解析 ───────────────────────────────────────────────────────
def test_parse_translations_plain():
    m = parse_translations('{"translations":[{"id":"a","text":"甲"}]}')
    assert m == {"a": "甲"}


def test_parse_translations_with_fence():
    m = parse_translations('```json\n{"translations":[{"id":"a","text":"甲"}]}\n```')
    assert m == {"a": "甲"}


def test_parse_translations_bare_array():
    assert parse_translations('[{"id":"a","text":"甲"}]') == {"a": "甲"}


def test_parse_translations_invalid_raises():
    with pytest.raises(Exception):
        parse_translations("这不是 JSON")


# ── DeepSeek Provider（注入 transport，不联网）────────────────────────
def _stub(content: str, tokens_in: int = 1000, tokens_out: int = 500):
    def transport(messages, model):
        return content, {"prompt_tokens": tokens_in, "completion_tokens": tokens_out}

    return transport


def test_deepseek_pricing_and_cost():
    p = DeepSeekProvider("k", model="deepseek-flash", price_tier="peak")
    # 高峰：输入 1 元/百万（缓存未命中）×2，输出 4 元/百万 ×2
    cost = p.estimate_cost(1_000_000, 1_000_000)
    assert cost == pytest.approx(2.0 + 8.0)
    assert p.price_of()["out"] == 4.0


def test_deepseek_translate_ok():
    p = DeepSeekProvider("k", transport=_stub('{"translations":[{"id":"a","text":"甲"}]}'))
    outs, usage = p.translate([SegmentIn("a", "甲?")], BookContext())
    assert outs[0].translation == "甲"
    assert usage.tokens_in == 1000 and usage.tokens_out == 500
    assert usage.cost > 0


def test_deepseek_missing_id_reported_as_error():
    p = DeepSeekProvider("k", transport=_stub('{"translations":[]}'))
    outs, _ = p.translate([SegmentIn("a", "x")], BookContext())
    assert outs[0].error


def test_deepseek_bad_json_raises_format_error():
    p = DeepSeekProvider("k", transport=_stub("完全不是 JSON"))
    with pytest.raises(TranslationFormatError):
        p.translate([SegmentIn("a", "x")], BookContext())


def test_deepseek_requires_key():
    with pytest.raises(ValueError):
        DeepSeekProvider("")


def test_extra_body_merged_into_request(monkeypatch):
    """本地 Qwen3 必须能带上 enable_thinking=false（否则返回空内容）。"""
    import httpx

    captured: dict = {}

    class FakeResp:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict:
            return {"choices": [{"message": {"content": '{"translations":[{"id":"a","text":"甲"}]}'}}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5}}

    def fake_post(url, headers=None, json=None, timeout=None):  # noqa: A002
        captured.update(json or {})
        return FakeResp()

    monkeypatch.setattr(httpx, "post", fake_post)
    p = DeepSeekProvider("k", extra_body={"chat_template_kwargs": {"enable_thinking": False}})
    outs, _ = p.translate([SegmentIn("a", "x")], BookContext())
    assert outs[0].translation == "甲"
    assert captured["chat_template_kwargs"] == {"enable_thinking": False}
    assert captured["model"] and captured["messages"]


# ── 端到端（Fake）───────────────────────────────────────────────────
def make_db(tmp_path: Path, n: int = 5):
    conn = connect(tmp_path / "t.db")
    blocks = [Block(id=f"b{i:06d}", type="paragraph", text=f"段落{i}") for i in range(1, n + 1)]
    ir = DocumentIR(doc=DocMeta(id="doc", title="测试书", source_lang="ja", origin="epub"),
                    blocks=blocks)
    import_ir(conn, ir)
    return conn


def test_run_fake_end_to_end(tmp_path: Path):
    conn = make_db(tmp_path)
    rep = run(conn, FakeProvider(), BookContext(doc_id="doc"), batch_items=2)
    assert rep.translated == 5
    assert rep.failed == 0
    assert rep.batches == 3
    s = stats(conn)
    assert s["done"] == 5 and s["by_status"].get("pending", 0) == 0
    row = conn.execute("SELECT * FROM segment WHERE seg_id='doc:b000001'").fetchone()
    assert row["translation"] == "[译]段落1"
    assert row["engine"] == "fake"
    assert s["tm_entries"] == 5


def test_run_retries_missing_then_succeeds(tmp_path: Path):
    conn = make_db(tmp_path, 3)
    provider = FakeProvider(fail_ids={"doc:b000002"}, fail_times=1)
    rep = run(conn, provider, BookContext(doc_id="doc"), batch_items=10)
    assert rep.translated == 3, "失败一次后应重试补齐"
    assert rep.failed == 0
    row = conn.execute("SELECT * FROM segment WHERE seg_id='doc:b000002'").fetchone()
    assert row["translation"] == "[译]段落2"


def test_run_marks_permanent_failure(tmp_path: Path):
    conn = make_db(tmp_path, 2)
    provider = FakeProvider(fail_ids={"doc:b000001"}, fail_times=99)
    rep = run(conn, provider, BookContext(doc_id="doc"), batch_items=10, max_rounds=2)
    assert rep.failed == 1 and rep.translated == 1
    row = conn.execute("SELECT * FROM segment WHERE seg_id='doc:b000001'").fetchone()
    assert row["status"] == "failed"
    assert row["last_error"]


def test_dry_run_does_not_call_provider(tmp_path: Path):
    conn = make_db(tmp_path, 4)
    provider = FakeProvider()
    rep = run(conn, provider, BookContext(doc_id="doc"), dry_run=True)
    assert rep.dry_run and rep.est_tokens_in > 0
    assert provider.calls == [], "干跑绝不能真调用"
    assert stats(conn)["done"] == 0


def test_max_cost_guardrail_stops(tmp_path: Path):
    """用带定价的 provider + 高用量 stub 触发护栏。"""
    conn = make_db(tmp_path, 6)
    calls = {"n": 0}

    def transport(messages, model):
        calls["n"] += 1
        ids = [json.loads(messages[1]["content"].split("输入段落：")[1])][0]
        return (json.dumps({"translations": [{"id": i["id"], "text": "译"} for i in ids]},
                           ensure_ascii=False),
                {"prompt_tokens": 200_000, "completion_tokens": 100_000})

    provider = DeepSeekProvider("k", model="deepseek-v4-pro", price_tier="peak", transport=transport)
    rep = run(conn, provider, BookContext(doc_id="doc"), batch_items=1, max_cost=1.0)
    assert rep.stopped.startswith("已达成本上限")
    assert calls["n"] < 6, "达到上限后不应继续调用"
    assert rep.usage.cost >= 1.0
