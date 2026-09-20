"""M3：未译重试护栏。

模型偶发把长段原文**原样返回**。这类问题不报错、状态还是 `done`，
只有 `tp qa` 事后能看出来（实测 1/3216 段）。

护栏做两件事：
1. 就地识别（喂给 QA 用的同一判定，避免口径分裂）；
2. 用**强指令**重试——原样重发同样的请求大概率得到同样的结果。
"""

from __future__ import annotations

import pytest

from transbook.store import connect, import_ir
from transbook.textutil import is_untranslated
from transbook.translate import BookContext
from transbook.translate.fake import FakeProvider
from transbook.translate.runner import GUARD_MIN_CHARS, run

LONG_JA = "また、『魔女』の声が聞こえた。── 次の加害者が、白目を剥いて倒れるのが見えた。" * 2


# ── 判定 ────────────────────────────────────────────────────────────
def test_identical_kana_text_is_untranslated():
    assert is_untranslated(LONG_JA, LONG_JA, min_chars=20)


def test_symbol_only_text_is_not_untranslated():
    """`「────」` 译成同样内容是**正确**的（早期版本误报 115 条）。"""
    assert not is_untranslated("「────」", "「────」", min_chars=0)
    assert not is_untranslated("第七章 『Reweave』", "第七章 『Reweave』", min_chars=0)


def test_short_kana_fragment_below_threshold():
    """`「べ」` 这类拟声片段原样保留无可厚非，不值得花 token 重试。"""
    assert not is_untranslated("「べ」", "「べ」", min_chars=20)
    assert is_untranslated("「べ」", "「べ」", min_chars=0)


def test_real_translation_is_not_flagged():
    assert not is_untranslated(LONG_JA, "而且，她听见了『魔女』的声音。")


def test_whitespace_differences_are_ignored():
    """模型把原文的换行/空格重排一遍，仍然是"没翻"，不该逃过护栏。"""
    spaced = "あ い う え お か き く け こ さ し す せ そ た ち つ て と な に ぬ ね の"
    assert is_untranslated(spaced, spaced.replace(" ", ""), min_chars=20)


# ── 护栏行为 ────────────────────────────────────────────────────────
@pytest.fixture
def db(tmp_path):
    conn = connect(tmp_path / "t.db")
    yield conn
    conn.close()


def _seed(db, texts):
    from transbook.ir import Block, DocumentIR, DocMeta

    ir = DocumentIR(
        doc=DocMeta(id="doc", title="t", source_lang="ja", origin="epub"),
        blocks=[Block(id=f"b{i:06d}", type="paragraph", text=t)
                for i, t in enumerate(texts, start=1)],
    )
    import_ir(db, ir)
    return [f"doc:b{i:06d}" for i in range(1, len(texts) + 1)]


def test_guard_retries_with_strict_prompt_and_recovers(db):
    """关键：重试必须发生在 `strict=True` 那一轮，否则等于白重试。"""
    ids = _seed(db, [LONG_JA, "普通の段落です。"])
    prov = FakeProvider(echo_ids={ids[0]})
    rep = run(db, prov, BookContext(doc_id="doc"), batch_items=10)

    assert rep.guarded == 1, "应识别出 1 段原样返回"
    assert rep.still_untranslated == 0, "强指令重试后应修好"
    assert rep.translated == 2
    assert prov.strict_calls == [False, True], "第二轮必须带 strict 标记"
    row = db.execute("SELECT translation FROM segment WHERE seg_id=?", (ids[0],)).fetchone()
    assert row["translation"] == f"[译]{LONG_JA}"


def test_guard_gives_up_but_keeps_translation(db):
    """重试仍不修好时：保留译文（不能变空），并记账让 QA 看得见。"""
    ids = _seed(db, [LONG_JA])

    class AlwaysEcho(FakeProvider):
        def translate(self, items, ctx, *, strict=False):
            self.calls.append([i.seg_id for i in items])
            self.strict_calls.append(strict)
            from transbook.translate.base import SegmentOut, Usage

            return [SegmentOut(i.seg_id, translation=i.text) for i in items], Usage(calls=1)

    prov = AlwaysEcho()
    rep = run(db, prov, BookContext(doc_id="doc"), batch_items=10, max_rounds=3)

    assert rep.guarded == 3, "每轮都识别到，共 3 轮"
    assert rep.still_untranslated == 1
    assert rep.failed == 0, "有译文就不该记成失败"
    row = db.execute("SELECT translation, status FROM segment WHERE seg_id=?",
                     (ids[0],)).fetchone()
    assert row["translation"] == LONG_JA
    assert row["status"] == "done"


def test_guard_ignores_short_fragments(db):
    """短拟声片段不触发重试（阈值可调）。"""
    ids = _seed(db, ["「べ」"])
    prov = FakeProvider(echo_ids={ids[0]})
    rep = run(db, prov, BookContext(doc_id="doc"), batch_items=10)
    assert rep.guarded == 0
    assert prov.strict_calls == [False]


def test_guard_threshold_is_configurable(db):
    """把阈值降到 0，短拟声片段也会进护栏——证明闸门确实起作用。"""
    ids = _seed(db, ["「べ」"])
    prov = FakeProvider(echo_ids={ids[0]})
    rep = run(db, prov, BookContext(doc_id="doc"), batch_items=10,
              guard_min_chars=0, max_rounds=2)
    assert rep.guarded == 1, "第一轮识别到并重试"
    assert rep.still_untranslated == 0, "strict 那轮修好了"
    assert prov.strict_calls == [False, True]
    assert GUARD_MIN_CHARS == 20, "默认阈值仍是 20"


def test_error_retry_still_works(db):
    """原有"缺号/失败重试"不能被护栏改动破坏。"""
    ids = _seed(db, ["段落一です。", "段落二です。"])
    prov = FakeProvider(fail_ids={ids[0]}, fail_times=1)
    rep = run(db, prov, BookContext(doc_id="doc"), batch_items=10)
    assert rep.translated == 2 and rep.failed == 0 and rep.retried == 1
    assert rep.guarded == 0
