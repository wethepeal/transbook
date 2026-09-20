"""M4：EPUB 封面页。

EPUB3 要求封面图在清单里带 `properties="cover-image"`，并且最好有一个独立的
封面 XHTML 作为书脊第一项（多数阅读器的书架缩略图取的就是它）；
EPUB2 的老阅读器则只认 `<meta name="cover">`。三者都要有。
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest
from lxml import etree

from transbook.ir import Block, DocumentIR, DocMeta
from transbook.render.epub import COVER_HREF, write_epub
from transbook.render.xhtml import CSS, build_chapters, build_cover, build_nav

OPF_NS = "http://www.idpf.org/2007/opf"
DC_NS = "http://purl.org/dc/elements/1.1/"
OPS_NS = "http://www.idpf.org/2007/ops"
XHTML_NS = "http://www.w3.org/1999/xhtml"


def make_ir(**kw) -> DocumentIR:
    blocks = [
        Block(id="b000001", type="heading", level=1, text="第一章"),
        Block(id="b000002", type="paragraph", text="原文段落。"),
    ]
    blocks += kw.pop("extra", [])
    return DocumentIR(doc=DocMeta(id="doc", title="测试书", author="作者",
                                  source_lang="ja", origin="epub"), blocks=blocks)


def write(tmp_path: Path, ir: DocumentIR, cover: str | None, assets: dict[str, bytes]):
    adir = tmp_path / "assets"
    adir.mkdir(exist_ok=True)
    for name, data in assets.items():
        (adir / name).write_bytes(data)
    chapters = build_chapters(ir, {"b000002": "译文段落。"}, mode="zh")
    return write_epub(tmp_path / "out.epub", title=ir.doc.title, author=ir.doc.author,
                      language="zh", chapters=chapters, css=CSS,
                      nav=build_nav(chapters), images_dir=adir, cover_image=cover)


# ── 封面页 XHTML ────────────────────────────────────────────────────
def test_build_cover_uses_epub_type():
    raw = build_cover("我的书", "../images/cover.jpg")
    root = etree.fromstring(raw)
    body = next(e for e in root.iter() if e.tag == f"{{{XHTML_NS}}}body")
    assert body.get(f"{{{OPS_NS}}}type") == "cover"
    img = next(e for e in root.iter() if e.tag == f"{{{XHTML_NS}}}img")
    assert img.get("src") == "../images/cover.jpg"
    assert img.get("alt") == "我的书"


def test_build_cover_declares_epub_prefix():
    """`epub:type` 必须带命名空间声明，否则阅读器不认。"""
    head = build_cover("书", "../images/c.jpg").decode()
    assert 'xmlns:epub="http://www.idpf.org/2007/ops"' in head


# ── 打包 ────────────────────────────────────────────────────────────
def test_cover_image_declared_and_first_in_spine(tmp_path):
    ir = make_ir()
    out = write(tmp_path, ir, "cover.jpg", {"cover.jpg": b"x", "p1.jpg": b"y"})
    z = zipfile.ZipFile(out)
    names = z.namelist()
    assert f"OEBPS/{COVER_HREF}" in names, "应生成独立的封面页"
    opf = etree.fromstring(z.read("OEBPS/content.opf"))
    z.close()

    items = {e.get("id"): e for e in opf.iter(f"{{{OPF_NS}}}item")}
    assert items["cover-image"].get("properties") == "cover-image"
    assert items["cover-image"].get("href") == "images/cover.jpg"

    spine = [e.get("idref") for e in opf.iter(f"{{{OPF_NS}}}itemref")]
    assert spine[0] == "cover", "封面必须是书脊第一项"

    # EPUB2 兼容 meta
    metas = {e.get("name"): e.get("content") for e in opf.iter(f"{{{OPF_NS}}}meta")}
    assert metas.get("cover") == "cover-image"


def test_no_duplicate_manifest_ids(tmp_path):
    """封面图同时出现在 images 列表里，登记两次会产生重复 id（epubcheck 会报错）。"""
    out = write(tmp_path, make_ir(), "cover.jpg", {"cover.jpg": b"x", "p1.jpg": b"y"})
    z = zipfile.ZipFile(out)
    opf = etree.fromstring(z.read("OEBPS/content.opf"))
    z.close()
    ids = [e.get("id") for e in opf.iter(f"{{{OPF_NS}}}item")]
    assert len(ids) == len(set(ids)), f"清单 id 重复：{ids}"
    hrefs = [e.get("href") for e in opf.iter(f"{{{OPF_NS}}}item")]
    assert hrefs.count("images/cover.jpg") == 1, "封面图只能登记一次"


def test_cover_page_actually_references_the_image(tmp_path):
    out = write(tmp_path, make_ir(), "cover.jpg", {"cover.jpg": b"x"})
    z = zipfile.ZipFile(out)
    page = z.read(f"OEBPS/{COVER_HREF}").decode()
    z.close()
    assert "../images/cover.jpg" in page


def test_without_cover_nothing_changes(tmp_path):
    """向后兼容：不给封面时不生成封面页，书脊第一项仍是正文。"""
    out = write(tmp_path, make_ir(), None, {"p1.jpg": b"y"})
    z = zipfile.ZipFile(out)
    assert f"OEBPS/{COVER_HREF}" not in z.namelist()
    opf = etree.fromstring(z.read("OEBPS/content.opf"))
    z.close()
    spine = [e.get("idref") for e in opf.iter(f"{{{OPF_NS}}}itemref")]
    assert spine[0] != "cover"
    assert not any(e.get("properties") == "cover-image"
                   for e in opf.iter(f"{{{OPF_NS}}}item"))


def test_cover_not_in_assets_is_ignored(tmp_path):
    """封面文件名写错时不能凭空造一个指向不存在文件的封面页。"""
    out = write(tmp_path, make_ir(), "missing.jpg", {"p1.jpg": b"y"})
    z = zipfile.ZipFile(out)
    assert f"OEBPS/{COVER_HREF}" not in z.namelist()
    opf = etree.fromstring(z.read("OEBPS/content.opf"))
    z.close()
    assert not any(e.get("properties") == "cover-image"
                   for e in opf.iter(f"{{{OPF_NS}}}item"))


# ── 封面图识别 ──────────────────────────────────────────────────────
def test_cover_image_from_matter():
    ir = make_ir(extra=[
        Block(id="b000010", type="image", path="../image/kuchie-001.jpg", matter="front"),
        Block(id="b000011", type="image", path="../image/hyoshi.jpg", matter="cover"),
    ])
    assert ir.cover_image() == "hyoshi.jpg"


def test_cover_image_falls_back_to_filename():
    """老 IR 没有 matter 信息时，靠文件名兜底。"""
    ir = make_ir(extra=[
        Block(id="b000010", type="image", path="../image/p015.jpg"),
        Block(id="b000011", type="image", path="../image/cover.jpg"),
    ])
    assert ir.cover_image() == "cover.jpg"


def test_cover_image_none_when_absent():
    ir = make_ir(extra=[Block(id="b000010", type="image", path="../image/p015.jpg")])
    assert ir.cover_image() is None


def test_no_dangling_refs_including_nav(tmp_path):
    """`nav.xhtml` 在 OEBPS/ 下，样式表只能写 `style.css`。

    写成 `../style.css` 会逃出包外——内置校验器实测抓到过这个 bug，
    这里把它固化成回归测试。
    """
    out = write(tmp_path, make_ir(), "cover.jpg", {"cover.jpg": b"x"})
    from transbook.validate import validate_epub

    # 只跑内置检查：这里的图是占位字节，交给 epubcheck 会（正确地）报图片损坏
    rep = validate_epub(out, run_epubcheck_check=False)
    dangling = [i for i in rep.issues if i.code == "dangling-ref"]
    assert not dangling, f"存在悬空引用：{[str(i) for i in dangling]}"
    assert not rep.errors, [str(i) for i in rep.errors]

    z = zipfile.ZipFile(out)
    nav = z.read("OEBPS/nav.xhtml").decode()
    z.close()
    assert 'href="style.css"' in nav
