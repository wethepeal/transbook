"""EPUB 抽取测试：NAV 锚点结构、rt 剥离、空段落剔除、图片、竖排标记。"""

from __future__ import annotations

from pathlib import Path

from transbook.ingest import EpubIngestor


def test_metadata_and_source(minimal_epub: Path):
    ir = EpubIngestor(minimal_epub).extract()
    assert ir.doc.title == "测试之书"
    assert ir.doc.author == "某作者"
    assert ir.doc.source_lang == "ja"
    assert ir.doc.origin == "epub"
    assert ir.doc.spine_docs == 2


def test_vertical_detected_from_css(minimal_epub: Path):
    ir = EpubIngestor(minimal_epub).extract()
    assert ir.doc.vertical is True


def test_toc_from_nav_anchors(minimal_epub: Path):
    """核心：结构取自 NAV，标题文本必须与 NAV 完全一致（证明 rt 已剥离）。"""
    ir = EpubIngestor(minimal_epub).extract()
    assert [t.title for t in ir.toc] == ["第一章 『幕間』", "第二章 『氷上決戦』"]
    assert all(t.block_id for t in ir.toc), "每个目录条目都应链接到一个标题块"


def test_heading_blocks_use_nav_title(minimal_epub: Path):
    ir = EpubIngestor(minimal_epub).extract()
    heads = [b for b in ir.blocks if b.type == "heading"]
    assert [h.text for h in heads] == ["第一章 『幕間』", "第二章 『氷上決戦』"]
    assert all(h.src == "nav" for h in heads)


def test_blank_paragraphs_dropped(minimal_epub: Path):
    """`<p><br/></p>` 与全角空格段落都必须剔除，否则会污染翻译与成本。"""
    ir = EpubIngestor(minimal_epub).extract()
    paras = [b.text for b in ir.blocks if b.type == "paragraph"]
    assert all(p.strip() for p in paras)
    assert "最初の段落です。" in paras
    assert "二番目の段落で、漢字を含みます。" in paras  # rt 已剥离


def test_image_block(minimal_epub: Path):
    ir = EpubIngestor(minimal_epub).extract()
    imgs = [b for b in ir.blocks if b.type == "image"]
    assert len(imgs) == 1
    assert imgs[0].path == "images/pic.png"


def test_assets_extracted(minimal_epub: Path, tmp_path: Path):
    assets = tmp_path / "assets"
    EpubIngestor(minimal_epub).extract(assets_dir=assets)
    assert (assets / "pic.png").is_file(), "图片应被提取到 assets/"


def test_ruby_drop_counted(minimal_epub: Path):
    ir = EpubIngestor(minimal_epub).extract()
    assert ir.doc.ruby_dropped >= 3  # 幕/間/漢字 三处


def test_block_ids_are_stable_and_unique(minimal_epub: Path):
    a = EpubIngestor(minimal_epub).extract()
    b = EpubIngestor(minimal_epub).extract()
    assert [x.id for x in a.blocks] == [x.id for x in b.blocks], "同一输入必须产出相同 ID"
    ids = [x.id for x in a.blocks]
    assert len(ids) == len(set(ids))


def test_translatable_excludes_images(minimal_epub: Path):
    ir = EpubIngestor(minimal_epub).extract()
    assert all(b.type != "image" for b in ir.translatable())
