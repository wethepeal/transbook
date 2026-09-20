"""M4：EPUB 结构校验器。

用**故意写坏的包**验证每一类错误都能被抓到——校验器本身写错的话，
"全绿"就是假的。
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from transbook.validate import find_epubcheck, validate_epub

MIMETYPE = "application/epub+zip"
#: 最小合法 PNG（1×1）——epubcheck 会校验图片文件头，用假字节会被判 MED-004
PNG_1PX = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c6300010000050001"
    "0d0a2db40000000049454e44ae426082"
)
CONTAINER = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="OEBPS/content.opf"
      media-type="application/oebps-package+xml"/></rootfiles>
</container>
"""


def build(tmp_path: Path, *, opf: str = "", extra: dict[str, bytes | str] | None = None,
          stored_mimetype: bool = True, mimetype_first: bool = True,
          chapter: str | None = None) -> Path:
    """写一个最小 EPUB；参数用来注入各类缺陷。"""
    opf = opf or """<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="bookid">urn:uuid:test</dc:identifier>
    <dc:title>测试</dc:title><dc:language>zh</dc:language>
  </metadata>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="css" href="style.css" media-type="text/css"/>
    <item id="ch1" href="text/ch1.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine><itemref idref="ch1"/></spine>
</package>
"""
    chapter = chapter or ('<?xml version="1.0" encoding="UTF-8"?>\n'
                          '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>t</title>'
                          '<link rel="stylesheet" href="../style.css"/></head>'
                          '<body><p>正文</p></body></html>')
    nav = ('<?xml version="1.0" encoding="UTF-8"?>\n'
           '<html xmlns="http://www.w3.org/1999/xhtml" '
           'xmlns:epub="http://www.idpf.org/2007/ops"><head><title>目录</title>'
           '<link rel="stylesheet" href="style.css"/></head>'
           '<body><nav epub:type="toc"><ol><li><a href="text/ch1.xhtml">第一章</a></li>'
           '</ol></nav></body></html>')

    out = tmp_path / "t.epub"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        def mt() -> None:
            z.writestr(zipfile.ZipInfo("mimetype"), MIMETYPE,
                       compress_type=zipfile.ZIP_STORED if stored_mimetype
                       else zipfile.ZIP_DEFLATED)

        if mimetype_first:
            mt()
        z.writestr("META-INF/container.xml", CONTAINER)
        z.writestr("OEBPS/content.opf", opf)
        z.writestr("OEBPS/nav.xhtml", nav)
        z.writestr("OEBPS/style.css", "body{}")
        z.writestr("OEBPS/text/ch1.xhtml", chapter)
        for name, data in (extra or {}).items():
            z.writestr(name, data)
        if not mimetype_first:
            mt()
    return out


def codes(path: Path) -> set[str]:
    """只取内置检查的结论——外部 epubcheck 是否安装不应影响单测结果。"""
    return {i.code for i in validate_epub(path, run_epubcheck_check=False).issues}


def test_valid_epub_has_no_errors(tmp_path):
    rep = validate_epub(build(tmp_path), run_epubcheck_check=False)
    assert not rep.errors, [str(i) for i in rep.errors]
    assert rep.xhtml == 2 and rep.images == 0


def test_mimetype_must_be_first(tmp_path):
    assert "mimetype-order" in codes(build(tmp_path, mimetype_first=False))


def test_mimetype_must_be_stored(tmp_path):
    assert "mimetype-compressed" in codes(build(tmp_path, stored_mimetype=False))


def test_dangling_reference_is_caught(tmp_path):
    bad = ('<?xml version="1.0" encoding="UTF-8"?>\n'
           '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>t</title>'
           '<link rel="stylesheet" href="../nope.css"/></head><body/></html>')
    rep = validate_epub(build(tmp_path, chapter=bad), run_epubcheck_check=False)
    assert any(i.code == "dangling-ref" for i in rep.errors)


def test_nav_css_must_not_escape_package(tmp_path):
    """回归：nav.xhtml 在 OEBPS/ 下写 `../style.css` 会指到包外。

    这条 bug 是内置校验器在真实成品上首跑就抓到的。
    """
    rep = validate_epub(build(tmp_path), run_epubcheck_check=False)
    assert not [i for i in rep.issues if i.code == "dangling-ref"], \
        [str(i) for i in rep.issues]


def test_missing_manifest_file_is_caught(tmp_path):
    opf = """<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="bookid">x</dc:identifier><dc:title>t</dc:title>
  </metadata>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="ghost" href="ghost.xhtml" media-type="application/xhtml+xml"/>
  </manifest><spine><itemref idref="ghost"/></spine>
</package>
"""
    assert "missing-item" in codes(build(tmp_path, opf=opf))


def test_duplicate_manifest_id_is_caught(tmp_path):
    opf = """<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="bookid">x</dc:identifier><dc:title>t</dc:title>
  </metadata>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="dup" href="style.css" media-type="text/css"/>
    <item id="dup" href="style.css" media-type="text/css"/>
  </manifest><spine><itemref idref="dup"/></spine>
</package>
"""
    assert "dup-id" in codes(build(tmp_path, opf=opf))


def test_bad_spine_idref_is_caught(tmp_path):
    opf = """<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="bookid">x</dc:identifier><dc:title>t</dc:title>
  </metadata>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
  </manifest><spine><itemref idref="nope"/></spine>
</package>
"""
    assert "bad-idref" in codes(build(tmp_path, opf=opf))


def test_malformed_xhtml_is_caught(tmp_path):
    rep = validate_epub(build(tmp_path, chapter="<html><body><p>未闭合"), run_epubcheck_check=False)
    assert any(i.code == "malformed" for i in rep.errors)


def test_cover_meta_without_item_is_caught(tmp_path):
    opf = """<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="bookid">x</dc:identifier><dc:title>t</dc:title>
    <meta name="cover" content="ghost-id"/>
  </metadata>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="ch1" href="text/ch1.xhtml" media-type="application/xhtml+xml"/>
  </manifest><spine><itemref idref="ch1"/></spine>
</package>
"""
    assert "bad-cover-meta" in codes(build(tmp_path, opf=opf))


def test_cover_image_without_meta_warns(tmp_path):
    """有 cover-image 但没有 EPUB2 的 meta —— 只是警告，不影响 EPUB3 合规。"""
    opf = """<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="bookid">x</dc:identifier><dc:title>t</dc:title>
  </metadata>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="ci" href="cover.jpg" media-type="image/jpeg" properties="cover-image"/>
    <item id="ch1" href="text/ch1.xhtml" media-type="application/xhtml+xml"/>
  </manifest><spine><itemref idref="ch1"/></spine>
</package>
"""
    rep = validate_epub(build(tmp_path, opf=opf, extra={"OEBPS/cover.jpg": b"x"}), run_epubcheck_check=False)
    assert "no-cover-meta" in {i.code for i in rep.warnings}
    assert not rep.errors


def test_missing_file_reports_cleanly(tmp_path):
    rep = validate_epub(tmp_path / "nope.epub")
    assert rep.errors and rep.errors[0].code == "missing"


def test_epubcheck_absence_is_reported_not_hidden(tmp_path, monkeypatch):
    """找不到 epubcheck 时必须**说明**，不能静默当作通过。"""
    import transbook.validate as v

    monkeypatch.setattr(v, "_epubcheck_argv", lambda epub: None)
    rep = v.validate_epub(build(tmp_path))
    assert rep.epubcheck_ran is False
    assert "未安装" in rep.epubcheck


def test_skip_switch_does_not_pretend_to_pass(tmp_path):
    """显式跳过时要写明"已跳过"，不能让人误以为跑过了。"""
    rep = validate_epub(build(tmp_path), run_epubcheck_check=False)
    assert rep.epubcheck_ran is False
    assert "跳过" in rep.epubcheck


@pytest.mark.skipif(find_epubcheck() is None, reason="本机未装配 epubcheck")
def test_epubcheck_runs_when_installed(tmp_path):
    """装了 epubcheck 就真的跑一遍——本项目的成品应零错误零警告。"""
    from transbook.render.epub import write_epub as _w
    from transbook.render.xhtml import CSS, build_chapters, build_nav

    from transbook.ir import Block, DocumentIR, DocMeta

    ir = DocumentIR(doc=DocMeta(id="d", title="书", author="作者", source_lang="ja",
                                origin="epub"),
                    blocks=[Block(id="b000001", type="paragraph", text="原文。")])
    chapters = build_chapters(ir, {"b000001": "译文。"}, mode="zh")
    assets = tmp_path / "assets"
    assets.mkdir()
    # 必须是**真图片**：epubcheck 会校验文件头（用 b"x" 会被判 MED-004 损坏）
    (assets / "cover.png").write_bytes(PNG_1PX)
    out = _w(tmp_path / "o.epub", title="书", author="作者", language="zh",
             chapters=chapters, css=CSS, nav=build_nav(chapters), images_dir=assets,
             cover_image="cover.png",
             identifier="urn:uuid:12345678-1234-1234-1234-123456789abc")
    rep = validate_epub(out)
    assert rep.epubcheck_ran is True
    assert not rep.errors, [str(i) for i in rep.errors]


def test_find_epubcheck_returns_none_or_jar():
    jar = find_epubcheck()
    assert jar is None or jar.name.endswith(".jar")
