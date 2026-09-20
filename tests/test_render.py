"""渲染层测试：XHTML 生成、双语/纯中文模式、EPUB 打包与**回环再解析**。"""

from __future__ import annotations

import zipfile
from pathlib import Path

from transbook.ingest import EpubIngestor
from transbook.ir import Block, DocMeta, DocumentIR
from transbook.render import CSS, build_chapters, build_nav, write_epub


def make_ir() -> DocumentIR:
    blocks = [
        Block(id="b000001", type="heading", level=1, text="第一章 序"),
        Block(id="b000002", type="paragraph", text="原文一段。"),
        Block(id="b000003", type="paragraph", text="原文二段。"),
        Block(id="b000004", type="image", path="images/pic.png"),
        Block(id="b000005", type="heading", level=1, text="第二章"),
        Block(id="b000006", type="paragraph", text="第二章原文。"),
    ]
    return DocumentIR(doc=DocMeta(id="doc", title="测试书", author="作者", source_lang="ja",
                                  origin="epub"), blocks=blocks)


TR = {"b000002": "译文一段。", "b000003": "译文二段。", "b000006": "第二章译文。"}


def test_chapters_split_on_headings():
    chs = build_chapters(make_ir(), TR, mode="bilingual")
    assert [c.title for c in chs] == ["第一章 序", "第二章"]
    assert [c.filename for c in chs] == ["text/ch0001.xhtml", "text/ch0002.xhtml"]


def test_bilingual_contains_both_languages():
    xml = build_chapters(make_ir(), TR, mode="bilingual")[0].content.decode("utf-8")
    assert "原文一段。" in xml and "译文一段。" in xml
    assert "bi-src" in xml and "bi-tgt" in xml


def test_zh_mode_drops_source():
    xml = build_chapters(make_ir(), TR, mode="zh")[0].content.decode("utf-8")
    assert "译文一段。" in xml
    assert "原文一段。" not in xml, "纯中文版不应出现原文"


def test_zh_mode_skips_untranslated():
    xml = build_chapters(make_ir(), {}, mode="zh")[0].content.decode("utf-8")
    assert "原文一段。" not in xml and "译文一段。" not in xml


def test_nav_links_chapters():
    chs = build_chapters(make_ir(), TR, mode="zh")
    nav = build_nav(chs).decode("utf-8")
    assert "text/ch0001.xhtml" in nav and "第一章 序" in nav


def test_epub_structure(tmp_path: Path):
    chs = build_chapters(make_ir(), TR, mode="zh")
    out = write_epub(tmp_path / "t.epub", title="测试书", author="作者", language="zh",
                     chapters=chs, css=CSS, nav=build_nav(chs))
    assert out.stat().st_size > 0
    with zipfile.ZipFile(out) as z:
        names = z.namelist()
        # mimetype 必须第一个且不压缩（EPUB 规范）
        assert names[0] == "mimetype"
        assert z.getinfo("mimetype").compress_type == zipfile.ZIP_STORED
        assert z.read("mimetype") == b"application/epub+zip"
        assert "META-INF/container.xml" in names
        assert "OEBPS/content.opf" in names and "OEBPS/nav.xhtml" in names
        opf = z.read("OEBPS/content.opf").decode("utf-8")
        assert "text/ch0001.xhtml" in opf and 'properties="nav"' in opf
        assert opf.count("<itemref") == len(chs)


def test_epub_round_trip_reingest(tmp_path: Path):
    """回环验证：我们生成的 EPUB 能被自家抽取器重新读出（dogfooding）。"""
    chs = build_chapters(make_ir(), TR, mode="zh")
    out = write_epub(tmp_path / "t.epub", title="测试书", author="作者", language="zh",
                     chapters=chs, css=CSS, nav=build_nav(chs))
    ir2 = EpubIngestor(out).extract()
    texts = [b.text for b in ir2.blocks if b.type == "paragraph"]
    assert "译文一段。" in texts
    assert "第二章译文。" in texts
    assert ir2.toc, "重新解析应能从 nav 还原目录"
    assert {t.title for t in ir2.toc} == {"第一章 序", "第二章"}
