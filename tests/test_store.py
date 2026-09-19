"""存储层测试：段落导入、稳定 ID、TM 复用、断点续跑不覆盖译文。"""

from __future__ import annotations

from pathlib import Path

import pytest

from transbook.ir import Block, DocumentIR, DocMeta
from transbook.store import connect, import_ir, pending, record_translation, stats, text_hash_of


def make_ir(doc_id: str = "testdoc", texts: list[str] | None = None) -> DocumentIR:
    texts = texts or ["第一章 序", "最初の段落です。", "二番目の段落です。"]
    blocks = []
    for i, t in enumerate(texts, start=1):
        blocks.append(Block(id=f"b{i:06d}",
                            type="heading" if i == 1 else "paragraph", text=t,
                            level=1 if i == 1 else None))
    blocks.append(Block(id="b000099", type="image", path="images/x.png"))
    return DocumentIR(
        doc=DocMeta(id=doc_id, title="测试", source_lang="ja", origin="epub"),
        blocks=blocks,
    )


@pytest.fixture
def db(tmp_path: Path):
    conn = connect(tmp_path / "t.db")
    yield conn
    conn.close()


def test_import_creates_segments_for_translatable_only(db):
    st = import_ir(db, make_ir())
    assert st.created == 3
    assert st.skipped == 1  # 图片块不参与翻译
    rows = pending(db)
    assert len(rows) == 3
    assert all(r["status"] == "pending" for r in rows)


def test_seg_id_is_stable_and_readable(db):
    import_ir(db, make_ir())
    ids = [r["seg_id"] for r in pending(db)]
    assert ids == ["testdoc:b000001", "testdoc:b000002", "testdoc:b000003"]


def test_reimport_is_idempotent(db):
    ir = make_ir()
    import_ir(db, ir)
    st2 = import_ir(db, ir)
    assert st2.created == 0
    assert st2.updated == 0
    assert len(pending(db)) == 3


def test_reimport_preserves_translation(db):
    """核心保证：重新抽取后译文不丢（原文未变时）。"""
    import_ir(db, make_ir())
    record_translation(db, "testdoc:b000002", "最初的段落。", engine="deepseek", model="m")
    import_ir(db, make_ir())  # 再导入一次
    row = db.execute("SELECT * FROM segment WHERE seg_id='testdoc:b000002'").fetchone()
    assert row["translation"] == "最初的段落。"
    assert row["status"] == "done"


def test_text_hash_deterministic():
    assert text_hash_of("あ い") == text_hash_of("  あ   い  ")
    assert text_hash_of("a") != text_hash_of("b")


def test_tm_carry_over_is_cross_document(db):
    """TM 跨文档：另一本书里相同的句子应自动命中。"""
    import_ir(db, make_ir("bookA", ["共通の文です。"]))
    record_translation(db, "bookA:b000001", "这是共通句。", engine="deepseek")
    st = import_ir(db, make_ir("bookB", ["共通の文です。"]))
    assert st.carried == 1
    row = db.execute("SELECT * FROM segment WHERE seg_id='bookB:b000001'").fetchone()
    assert row["translation"] == "这是共通句。"
    assert row["status"] == "done"


def test_stats(db):
    import_ir(db, make_ir())
    record_translation(db, "testdoc:b000002", "译一", engine="deepseek", tokens_in=10,
                       tokens_out=20, cost=0.001)
    s = stats(db)
    assert s["n"] == 3
    assert s["done"] == 1
    assert s["by_status"]["pending"] == 2
    assert pytest.approx(s["cost"], rel=1e-6) == 0.001
    assert s["tm_entries"] == 1


def test_record_translation_does_not_touch_final(db):
    import_ir(db, make_ir())
    db.execute("UPDATE segment SET final_translation='人工定稿' WHERE seg_id='testdoc:b000002'")
    record_translation(db, "testdoc:b000002", "机器译文", engine="deepseek")
    row = db.execute("SELECT * FROM segment WHERE seg_id='testdoc:b000002'").fetchone()
    assert row["translation"] == "机器译文"
    assert row["final_translation"] == "人工定稿"
