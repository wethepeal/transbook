"""审核回流测试：TSV 导出、按 seg_id 回灌、定稿不覆盖机翻、备份、回滚。"""

from __future__ import annotations

from pathlib import Path

from transbook.ir import Block, DocMeta, DocumentIR
from transbook.review import apply_tsv, clear_final, export_markdown, export_tsv
from transbook.store import connect, import_ir, record_translation


def make_db(tmp_path: Path, texts: list[str] | None = None):
    texts = texts or ["一段目。", "二段目。", "三段目。"]
    db = tmp_path / "t.db"
    conn = connect(db)
    ir = DocumentIR(doc=DocMeta(id="doc", title="测试书", source_lang="ja", origin="epub"),
                    blocks=[Block(id=f"b{i:06d}", type="paragraph", text=t)
                            for i, t in enumerate(texts, start=1)])
    import_ir(conn, ir)
    for i in range(1, len(texts) + 1):
        record_translation(conn, f"doc:b{i:06d}", f"机翻{i}", engine="fake")
    return db, conn


def test_export_tsv_shape(tmp_path: Path):
    db, conn = make_db(tmp_path)
    p = tmp_path / "review.tsv"
    n = export_tsv(conn, p)
    assert n == 3
    lines = [ln for ln in p.read_text(encoding="utf-8").splitlines() if not ln.startswith("#")]
    assert lines[0].split("\t") == ["seg_id", "status", "source", "translation"]
    assert lines[1].split("\t")[0] == "doc:b000001"
    assert lines[1].split("\t")[3] == "机翻1"


def test_apply_only_changed_rows(tmp_path: Path):
    db, conn = make_db(tmp_path)
    p = tmp_path / "review.tsv"
    export_tsv(conn, p)
    text = p.read_text(encoding="utf-8").replace("机翻2", "人工定稿二")
    p.write_text(text, encoding="utf-8")

    st = apply_tsv(conn, p, db_path=db)
    assert st.updated == 1
    assert st.unchanged == 2
    assert st.unknown == 0
    row = conn.execute("SELECT * FROM segment WHERE seg_id='doc:b000002'").fetchone()
    assert row["final_translation"] == "人工定稿二"
    assert row["translation"] == "机翻2", "机翻必须原样保留"
    assert row["status"] == "reviewed"
    # 未改动的行不能被写成定稿
    other = conn.execute("SELECT * FROM segment WHERE seg_id='doc:b000001'").fetchone()
    assert other["final_translation"] is None


def test_apply_makes_backup(tmp_path: Path):
    db, conn = make_db(tmp_path)
    p = tmp_path / "review.tsv"
    export_tsv(conn, p)
    st = apply_tsv(conn, p, db_path=db)
    assert st.backup is not None and Path(st.backup).is_file()


def test_apply_reports_unknown_seg_id(tmp_path: Path):
    db, conn = make_db(tmp_path)
    p = tmp_path / "review.tsv"
    p.write_text("seg_id\tstatus\tsource\ttranslation\n"
                 "doc:不存在\tdone\tx\t改了\n"
                 "doc:b000001\tdone\t一段目。\t新译\n", encoding="utf-8")
    st = apply_tsv(conn, p, db_path=db, backup=False)
    assert st.unknown == 1 and st.updated == 1


def test_apply_skips_blank_edits(tmp_path: Path):
    """留空 = 不改（而不是删掉译文），避免误清空。"""
    db, conn = make_db(tmp_path)
    p = tmp_path / "review.tsv"
    export_tsv(conn, p)
    p.write_text(p.read_text(encoding="utf-8").replace("机翻1", ""), encoding="utf-8")
    st = apply_tsv(conn, p, db_path=db, backup=False)
    assert st.blank == 1
    row = conn.execute("SELECT * FROM segment WHERE seg_id='doc:b000001'").fetchone()
    assert row["final_translation"] is None and row["translation"] == "机翻1"


def test_escaping_round_trip(tmp_path: Path):
    """含制表符/换行的译文必须能安全往返（不破坏 TSV 结构）。"""
    db, conn = make_db(tmp_path)
    weird = "含\t制表符\n与换行"
    conn.execute("UPDATE segment SET translation=? WHERE seg_id='doc:b000001'", (weird,))
    conn.commit()
    p = tmp_path / "review.tsv"
    export_tsv(conn, p)
    raw = p.read_text(encoding="utf-8")
    assert "含\\t制表符\\n与换行" in raw, "导出时应转义"
    st = apply_tsv(conn, p, db_path=db, backup=False)
    assert st.unchanged == 3, "转义后回灌应识别为未改动"


def test_clear_final_rolls_back_to_machine(tmp_path: Path):
    db, conn = make_db(tmp_path)
    p = tmp_path / "review.tsv"
    export_tsv(conn, p)
    p.write_text(p.read_text(encoding="utf-8").replace("机翻1", "人工"), encoding="utf-8")
    apply_tsv(conn, p, db_path=db, backup=False)
    assert clear_final(conn, doc_id="doc") == 1
    row = conn.execute("SELECT * FROM segment WHERE seg_id='doc:b000001'").fetchone()
    assert row["final_translation"] is None and row["status"] == "done"
    assert row["translation"] == "机翻1"


def test_export_markdown_readable(tmp_path: Path):
    db, conn = make_db(tmp_path)
    p = tmp_path / "review.md"
    assert export_markdown(conn, p) == 3
    md = p.read_text(encoding="utf-8")
    assert "doc:b000001" in md and "一段目。" in md and "机翻1" in md


def test_only_translated_filter(tmp_path: Path):
    db, conn = make_db(tmp_path)
    conn.execute("UPDATE segment SET translation=NULL, status='pending' WHERE seg_id='doc:b000001'")
    conn.commit()
    p = tmp_path / "review.tsv"
    n = export_tsv(conn, p, only_translated=True)
    assert n == 2
