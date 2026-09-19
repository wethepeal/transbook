"""术语抽取与 QA 检查测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from transbook.ir import Block, DocumentIR, DocMeta
from transbook.quality import check, extract_candidates, load_glossary, write_candidates
from transbook.store import connect, import_ir, record_translation


# ── 术语抽取 ────────────────────────────────────────────────────────
TEXTS = [
    "レムはアルデバランを見た。レムは微笑んだ。",
    "アルデバランは言った。レムは頷いた。",
    "『氷上決戦』が始まる。レムとアルデバラン。",
    "コーヒーを飲みながら、レムは考えた。",
]


def test_extract_katakana_and_quoted():
    cands = extract_candidates(TEXTS, min_count=2, include_kanji=False)
    terms = {c.term: c for c in cands}
    assert "レム" in terms and terms["レム"].count == 5
    assert "アルデバラン" in terms and terms["アルデバラン"].kind == "katakana"
    # 引号短语只出现一次 → min_count=2 时被过滤
    assert "氷上決戦" not in terms
    once = {c.term: c for c in extract_candidates(TEXTS, min_count=1, include_kanji=False)}
    assert "氷上決戦" in once and once["氷上決戦"].kind == "quoted"


def test_extract_filters_stopwords_and_min_count():
    cands = extract_candidates(TEXTS, min_count=2, include_kanji=False)
    assert "コーヒー" not in {c.term for c in cands}, "停用词应被过滤"
    single = extract_candidates(["一度だけ出るワード"], min_count=2)
    assert "ワード" not in {c.term for c in single}, "低于 min_count 的不应入选"


def test_extract_kanji_toggle():
    with_kanji = extract_candidates(TEXTS, min_count=2, include_kanji=True)
    without = extract_candidates(TEXTS, min_count=2, include_kanji=False)
    assert len(with_kanji) >= len(without)


def test_write_candidates_format(tmp_path: Path):
    p = tmp_path / "c.tsv"
    n = write_candidates(p, extract_candidates(TEXTS, min_count=2, include_kanji=False))
    text = p.read_text(encoding="utf-8")
    assert n > 0
    assert text.startswith("#")
    assert "source\tcount\tkind\tsample\ttranslation" in text
    assert any(ln.startswith("レム\t") for ln in text.splitlines())


# ── 术语表加载 ──────────────────────────────────────────────────────
def test_load_glossary_formats(tmp_path: Path):
    eq = tmp_path / "a.txt"
    eq.write_text("# 注释\nレム=蕾姆\nアルデバラン = 阿尔德巴兰\n", encoding="utf-8")
    g = load_glossary(eq)
    assert g == {"レム": "蕾姆", "アルデバラン": "阿尔德巴兰"}

    tsv = tmp_path / "b.tsv"
    tsv.write_text("source\tcount\tkind\tsample\ttranslation\n"
                   "レム\t9\tkatakana\t...\t蕾姆\n", encoding="utf-8")
    assert load_glossary(tsv) == {"レム": "蕾姆"}

    js = tmp_path / "c.json"
    js.write_text('{"レム": "蕾姆", "空": ""}', encoding="utf-8")
    assert load_glossary(js) == {"レム": "蕾姆"}

    assert load_glossary(None) == {}
    assert load_glossary(tmp_path / "不存在.txt") == {}


# ── QA 检查 ─────────────────────────────────────────────────────────
def _db_with(tmp_path: Path, rows: list[tuple[str, str | None, str]]):
    """rows: (源文, 译文, 状态)"""
    db = tmp_path / "t.db"
    conn = connect(db)
    ir = DocumentIR(doc=DocMeta(id="doc", title="测试", source_lang="ja", origin="epub"),
                    blocks=[Block(id=f"b{i:06d}", type="paragraph", text=src)
                            for i, (src, _, _) in enumerate(rows, start=1)])
    import_ir(conn, ir)
    for i, (_src, tgt, status) in enumerate(rows, start=1):
        seg = f"doc:b{i:06d}"
        if tgt is not None:
            record_translation(conn, seg, tgt, engine="fake")
        conn.execute("UPDATE segment SET status=? WHERE seg_id=?", (status, seg))
    conn.commit()
    return conn, ir


def test_qa_detects_all_kinds(tmp_path: Path):
    long_src = "長" * 80
    rows = [
        ("レムは微笑んだ。", "蕾姆微笑了。", "done"),          # 正常
        ("アルは言った。", None, "pending"),                   # untranslated(warn)
        ("夢を見た。", "夢を見た。", "done"),                   # source_leak
        ("夢の城に帰り着いた。", "回到了梦の城。", "done"),       # kana_left
        ("レムは強い。", "雷姆很强。", "done"),                 # term_missing（应为蕾姆）
        (long_src, "短。", "done"),                            # length_anomaly
        ("アルデバラン。", "アルデバラン很强。", "done"),         # katakana_left
    ]
    conn, ir = _db_with(tmp_path, rows)
    rep = check(conn, ir, {"レム": "蕾姆"})
    kinds = rep.counts()
    assert rep.checked == 7
    for expected in ("untranslated", "source_leak", "kana_left", "term_missing",
                     "length_anomaly", "katakana_left"):
        assert kinds.get(expected, 0) >= 1, f"应检出 {expected}，实际 {kinds}"
    # 正常的那段不应被误报
    assert not any(i.seg_id == "doc:b000001" for i in rep.issues)


def test_qa_term_consistency_pass(tmp_path: Path):
    conn, ir = _db_with(tmp_path, [("レムは強い。", "蕾姆很强。", "done")])
    rep = check(conn, ir, {"レム": "蕾姆"})
    assert rep.ok(), [str(i) for i in rep.issues]


def test_qa_id_mismatch(tmp_path: Path):
    conn, ir = _db_with(tmp_path, [("レム。", "蕾姆。", "done")])
    ir.blocks.append(Block(id="b999999", type="paragraph", text="未入库的段"))
    rep = check(conn, ir)
    assert rep.counts().get("id_mismatch") == 1
    assert not rep.ok()


def test_qa_skips_length_check_for_short_source(tmp_path: Path):
    """超短原文（「１」「──」）长度比不稳定，不应误报。"""
    conn, ir = _db_with(tmp_path, [("１", "一。", "done"), ("──", "——", "done")])
    rep = check(conn, ir)
    assert rep.counts().get("length_anomaly", 0) == 0


def test_extract_filters_common_kanji():
    """常见汉字词（自分/彼女）不应淹没候选表。"""
    cands = extract_candidates(["自分と彼女がレムを見た。" for _ in range(5)],
                               min_count=3, include_kanji=True)
    terms = {c.term for c in cands}
    assert "レム" in terms
    assert "自分" not in terms and "彼女" not in terms


def test_qa_summary_and_strict_semantics(tmp_path: Path):
    conn, ir = _db_with(tmp_path, [("夢を見た。", "夢を見た。", "done")])
    rep = check(conn, ir)
    assert not rep.ok()
    assert "source_leak" in rep.summary()
    conn2, ir2 = _db_with(tmp_path / "ok", [("レム。", "蕾姆。", "done")])
    rep2 = check(conn2, ir2)
    assert rep2.ok() and "全部通过" in rep2.summary()
