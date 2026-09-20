"""EPUB 抽取测试：NAV 锚点结构、rt 剥离、空段落剔除、图片、竖排标记。"""

from __future__ import annotations

import posixpath
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


# ── M2：前后附页（SVG 整页图 + 归类）────────────────────────────────
def test_svg_wrapped_images_are_captured(rich_epub: Path):
    """封面/口絵/裏表紙/广告是 `<svg><image xlink:href>`，**不是** `<img>`。

    只认 `<img>` 时这些图会被静默丢掉（实测真实样书漏 10/22 张，且成品里
    图片被打包却从不显示）。
    """
    ir = EpubIngestor(rich_epub).extract()
    imgs = [b for b in ir.blocks if b.type == "image"]
    names = sorted(posixpath.basename(b.path or "") for b in imgs)
    assert names == ["allcover-001.jpg", "cover.jpg", "i-bookwalker.jpg", "kuchie-001.jpg"]


def test_matter_classified(rich_epub: Path):
    """前后附页归类：路径线索优先，正文默认 main。"""
    ir = EpubIngestor(rich_epub).extract()
    c = ir.matter_counts()
    assert c["cover"] == 2, "封面页 = NAV 标题 + 整页图"
    assert c["front"] == 1, "口絵"
    assert c["back"] == 1, "裏表紙（allcover 里含 cover 子串，不能误判成封面）"
    assert c["promo"] == 1, "广告页"
    assert c["colophon"] == 3, "奥付 = 标题 + 2 段"
    assert c["toc"] == 4, "目次页 = 标题 + CONTENTS + 2 条章节名"
    assert c["main"] == 3, "正文章节 = 标题 + 2 段"


def test_every_image_block_is_referenced_in_output(rich_epub: Path, tmp_path: Path):
    """端到端：IR 里的每个 image 块都必须在成品 EPUB 里被 XHTML 引用。

    打包器是**整目录拷贝**，所以"文件在包里"不能证明图能显示——必须查引用。
    """
    import re
    import zipfile

    from transbook.render.epub import write_epub
    from transbook.render.xhtml import CSS, build_chapters, build_nav

    assets = tmp_path / "assets"
    ir = EpubIngestor(rich_epub).extract(assets_dir=assets)
    assert len(ir.doc.id) > 0
    chapters = build_chapters(ir, {}, mode="bilingual")
    out = write_epub(tmp_path / "out.epub", title=ir.doc.title, author=ir.doc.author,
                     language="zh", chapters=chapters, css=CSS,
                     nav=build_nav(chapters), images_dir=assets)

    pat = re.compile(r'(?:xlink:)?(?:href|src)="([^"]+\.(?:jpg|jpeg|png))"', re.I)
    z = zipfile.ZipFile(out)
    referenced = {posixpath.basename(h)
                  for n in z.namelist() if n.endswith(".xhtml")
                  for h in pat.findall(z.read(n).decode("utf-8", "ignore"))}
    packaged = {posixpath.basename(n) for n in z.namelist()
                if n.lower().endswith((".jpg", ".jpeg", ".png"))}
    z.close()
    assert packaged == {"cover.jpg", "kuchie-001.jpg", "allcover-001.jpg", "i-bookwalker.jpg"}
    assert referenced == packaged, f"打包但未显示: {packaged - referenced}"
