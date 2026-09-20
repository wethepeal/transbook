"""M2：脚注与表格（结构保留 + 单元格级翻译单元）。

真实样书（Re:Zero 43/44）里脚注与表格数均为 0，故夹具按 **EPUB3 标准写法**
自造（`<aside epub:type="footnote">` + `<a epub:type="noteref">`、含 `colspan` 的 `<table>`）。
"""

from __future__ import annotations

from pathlib import Path

from transbook.ingest import EpubIngestor
from transbook.ir import Block


def test_footnote_extracted_as_own_block(notes_epub: Path):
    ir = EpubIngestor(notes_epub).extract()
    notes = [b for b in ir.blocks if b.type == "footnote"]
    assert len(notes) == 1
    assert notes[0].text == "これは脚注の本文です。"
    assert notes[0].note_id == "fn1"


def test_footnote_body_not_duplicated_as_paragraph(notes_epub: Path):
    """脚注容器内部的 `<p>` 不能再被当成正文段落——否则脚注会被翻译两遍。"""
    ir = EpubIngestor(notes_epub).extract()
    paras = [b.text for b in ir.blocks if b.type == "paragraph"]
    assert "これは脚注の本文です。" not in paras
    assert paras == [
        "本文の段落で、脚注※1があります。",
        "脚注のあとの段落。",
    ]


def test_paragraph_records_noteref(notes_epub: Path):
    ir = EpubIngestor(notes_epub).extract()
    para = next(b for b in ir.blocks if b.type == "paragraph")
    assert para.refs == ["fn1"]


def test_table_structure_preserved(notes_epub: Path):
    """表格必须保留行列结构；压成纯文本就等于丢掉表格。"""
    ir = EpubIngestor(notes_epub).extract()
    tables = [b for b in ir.blocks if b.type == "table"]
    assert len(tables) == 1
    t = tables[0]
    assert t.rows == [["名前", "値"], ["アル", "10"], ["備考欄"]]
    assert t.caption == "能力値の比較"
    assert t.cell_spans[2][0] == [1, 2], "colspan=2 必须记下来"


def test_table_cells_are_separate_translation_units():
    """单元格各自成翻译单元：`{block_id}:r{r}c{c}`，且能取回自己的原文。"""
    t = Block(id="b000007", type="table",
              rows=[["名前", "値"], ["アル", "10"]])
    assert t.unit_ids() == ["b000007:r0c0", "b000007:r0c1", "b000007:r1c0", "b000007:r1c1"]
    assert t.unit_text("b000007:r1c1") == "10"
    assert t.unit_text("b000007:r0c0") == "名前"


def test_normal_block_unit_is_itself():
    b = Block(id="b000001", type="paragraph", text="本文")
    assert b.unit_ids() == ["b000001"]
    assert b.unit_text("b000001") == "本文"


def test_translatable_includes_table_but_not_empty():
    t = Block(id="b1", type="table", rows=[["a", ""]])
    assert t.is_translatable()
    assert not Block(id="b2", type="table", rows=[["", " "]]).is_translatable()
    assert not Block(id="b3", type="paragraph", text="  ").is_translatable()
    assert Block(id="b4", type="footnote", text="注").is_translatable()


# ── 端到端：单元格译文进入成品 ──────────────────────────────────────
def test_table_cells_render_with_own_translations(notes_epub: Path, tmp_path: Path):
    import zipfile

    from transbook.render.epub import write_epub
    from transbook.render.xhtml import CSS, build_chapters, build_nav

    ir = EpubIngestor(notes_epub).extract()
    t = next(b for b in ir.blocks if b.type == "table")
    tr = {f"{t.id}:r{r}c{c}": f"译{r}{c}" for r, row in enumerate(t.rows)
          for c in range(len(row))}
    tr["b000002"] = "正文译文。"
    tr["b000004"] = "脚注译文。"

    chapters = build_chapters(ir, tr, mode="zh")
    out = write_epub(tmp_path / "o.epub", title="t", author="a", language="zh",
                     chapters=chapters, css=CSS, nav=build_nav(chapters))
    z = zipfile.ZipFile(out)
    xhtml = "".join(z.read(n).decode("utf-8") for n in z.namelist() if n.endswith(".xhtml"))
    z.close()
    assert "<table" in xhtml and 'colspan="2"' in xhtml
    assert "译00" in xhtml and "译20" in xhtml, "每个单元格应取到自己的译文"
    assert "脚注译文。" in xhtml and 'id="fn1"' in xhtml
    assert 'href="#fn1"' in xhtml, "正文里应有指向脚注的跳转"


def test_typst_table_and_footnote(notes_epub: Path, tmp_path: Path):
    from transbook.render.pdf import _count_extras, build_typst

    ir = EpubIngestor(notes_epub).extract()
    t = next(b for b in ir.blocks if b.type == "table")
    src, *_ = build_typst(ir, {f"{t.id}:r0c0": "名字"}, mode="zh")
    assert "#table(columns: 2" in src
    assert "table.cell(colspan: 2)" in src, "合并单元格应转成 colspan"
    assert "名字" in src
    assert _count_extras(ir) == (1, 1)
