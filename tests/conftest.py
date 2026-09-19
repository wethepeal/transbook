"""测试夹具：用代码生成一个最小但结构完整的 EPUB。

刻意复刻 M0 实测发现的真实特征：
* 标题**不是** `<h1>`，而是带 `id` 锚点 + 类名的 `<p>`（与样书一致）
* 标题里含**逐字注音** `<ruby>…<rt>…</rt></ruby>`（验证必须剥离 rt 才能与 NAV 对上）
* 正文里有**空段落** `<p><br/></p>`（必须剔除）
* CSS 声明 `writing-mode: vertical-rl`（竖排标记）
* NAV 使用 `#fragment` 锚点
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

CONTAINER = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="item/standard.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>
"""

OPF = """<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>测试之书</dc:title>
    <dc:creator>某作者</dc:creator>
    <dc:language>ja</dc:language>
    <dc:publisher>某出版社</dc:publisher>
    <dc:identifier id="bookid">test-001</dc:identifier>
  </metadata>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="css" href="style.css" media-type="text/css"/>
    <item id="ch1" href="ch1.xhtml" media-type="application/xhtml+xml"/>
    <item id="ch2" href="ch2.xhtml" media-type="application/xhtml+xml"/>
    <item id="img1" href="images/pic.png" media-type="image/png"/>
  </manifest>
  <spine><itemref idref="ch1"/><itemref idref="ch2"/></spine>
</package>
"""

NAV = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
<head><title>目次</title></head>
<body><nav epub:type="toc"><ol>
  <li><a href="ch1.xhtml#toc-001">第一章 『幕間』</a></li>
  <li><a href="ch2.xhtml#toc-002">第二章 『氷上決戦』</a></li>
</ol></nav></body></html>
"""

# 关键：标题用 <p class="bold mfont font-110per" id="toc-001">，并且「幕間」是逐字注音
CH1 = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><head><title>ch1</title></head>
<body class="vrtl p-text"><div class="main">
  <div class="start-1em">
    <p class="bold mfont font-110per" id="toc-001">第一章 『<ruby>幕<rt>まく</rt>間<rt>あい</rt></ruby>』</p>
  </div>
  <p><br/></p>
  <p>最初の段落です。</p>
  <p>　</p>
  <p>二番目の段落で、<ruby>漢字<rt>かんじ</rt></ruby>を含みます。</p>
  <div class="h-indent-5em"><p>１</p></div>
  <p>三番目の段落。</p>
  <img src="images/pic.png" alt="挿絵"/>
</div></body></html>
"""

CH2 = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><head><title>ch2</title></head>
<body><div class="main">
  <p class="bold mfont font-110per" id="toc-002">第二章 『氷上決戦』</p>
  <p>第二章の本文。</p>
</div></body></html>
"""

CSS = "body { writing-mode: vertical-rl; }\n.bold { font-weight: bold; }\n"

PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c6300010000050001od".replace("od", "0d0a") + "0000000049454e44ae426082"
)


@pytest.fixture
def minimal_epub(tmp_path: Path) -> Path:
    """生成最小 EPUB 并返回路径。"""
    path = tmp_path / "sample.epub"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("mimetype", "application/epub+zip", zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml", CONTAINER)
        z.writestr("item/standard.opf", OPF)
        z.writestr("item/nav.xhtml", NAV)
        z.writestr("item/ch1.xhtml", CH1)
        z.writestr("item/ch2.xhtml", CH2)
        z.writestr("item/style.css", CSS)
        z.writestr("item/images/pic.png", PNG)
    return path
