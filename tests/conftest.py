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


# ── 第二个夹具：复刻真实样书（Re:Zero 44）的**前后附页结构** ──────────
# 真实书里封面/口絵/裏表紙/广告整页图是 `<svg><image xlink:href>`，
# 不是 `<img>`——只认 `<img>` 会漏掉 10/22 张图（实测踩过）。
RICH_OPF = """<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>附页之书</dc:title><dc:creator>某作者</dc:creator>
    <dc:language>ja</dc:language><dc:identifier id="bookid">test-002</dc:identifier>
  </metadata>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="css" href="style.css" media-type="text/css"/>
    <item id="cover" href="p-cover.xhtml" media-type="application/xhtml+xml" properties="svg"/>
    <item id="fm" href="p-fmatter-001.xhtml" media-type="application/xhtml+xml" properties="svg"/>
    <item id="toc" href="p-toc-001.xhtml" media-type="application/xhtml+xml"/>
    <item id="ch1" href="p-002.xhtml" media-type="application/xhtml+xml"/>
    <item id="back" href="p-allcover-001.xhtml" media-type="application/xhtml+xml" properties="svg"/>
    <item id="col" href="p-colophon.xhtml" media-type="application/xhtml+xml"/>
    <item id="promo" href="p-bookwalker.xhtml" media-type="application/xhtml+xml" properties="svg"/>
    <item id="i-cover" href="image/cover.jpg" media-type="image/jpeg"/>
    <item id="i-kuchie" href="image/kuchie-001.jpg" media-type="image/jpeg"/>
    <item id="i-all" href="image/allcover-001.jpg" media-type="image/jpeg"/>
    <item id="i-promo" href="image/i-bookwalker.jpg" media-type="image/jpeg"/>
  </manifest>
  <spine>
    <itemref idref="cover"/><itemref idref="fm"/><itemref idref="toc"/>
    <itemref idref="ch1"/><itemref idref="back"/><itemref idref="col"/>
    <itemref idref="promo"/>
  </spine>
</package>
"""

RICH_NAV = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
<head><title>目次</title></head>
<body><nav epub:type="toc"><ol>
  <li><a href="p-cover.xhtml">表紙</a></li>
  <li><a href="p-toc-001.xhtml">目次</a></li>
  <li><a href="p-002.xhtml#toc-002">第一章 『運命の夜』</a></li>
  <li><a href="p-colophon.xhtml">奥付</a></li>
</ol></nav></body></html>
"""


def _svg_page(img: str, extra: str = "") -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:xlink="http://www.w3.org/1999/xlink">
<head><title>svg</title></head>
<body><div class="main">
  <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 600 800" preserveAspectRatio="xMidYMid meet">
    <image width="600" height="800" xlink:href="{img}"/>
  </svg>
  {extra}
</div></body></html>
"""


RICH_TOC = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><head><title>toc</title></head>
<body><div class="main"><p class="bold mfont">CONTENTS</p>
<p>第一章 『運命の夜』</p><p>第二章 『光の萌し』</p></div></body></html>
"""

RICH_CH1 = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><head><title>ch1</title></head>
<body><div class="main">
  <p class="bold mfont font-110per" id="toc-002">第一章 『運命の夜』</p>
  <p>本文の一段落目。</p>
  <p>本文の二段落目。</p>
</div></body></html>
"""

RICH_COLOPHON = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><head><title>col</title></head>
<body><div class="main"><p>奥付</p><p>発行所 株式会社KADOKAWA</p></div></body></html>
"""


@pytest.fixture
def rich_epub(tmp_path: Path) -> Path:
    """带封面/口絵/目次/正文/裏表紙/奥付/广告的 EPUB（镜像真实样书）。"""
    path = tmp_path / "rich.epub"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("mimetype", "application/epub+zip", zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml", CONTAINER)
        z.writestr("item/standard.opf", RICH_OPF)
        z.writestr("item/nav.xhtml", RICH_NAV)
        z.writestr("item/p-cover.xhtml", _svg_page("../image/cover.jpg"))
        z.writestr("item/p-fmatter-001.xhtml", _svg_page("../image/kuchie-001.jpg"))
        z.writestr("item/p-toc-001.xhtml", RICH_TOC)
        z.writestr("item/p-002.xhtml", RICH_CH1)
        z.writestr("item/p-allcover-001.xhtml", _svg_page("../image/allcover-001.jpg"))
        z.writestr("item/p-colophon.xhtml", RICH_COLOPHON)
        z.writestr("item/p-bookwalker.xhtml", _svg_page("../image/i-bookwalker.jpg"))
        z.writestr("item/style.css", CSS)
        for name in ("cover.jpg", "kuchie-001.jpg", "allcover-001.jpg", "i-bookwalker.jpg"):
            z.writestr(f"item/image/{name}", PNG)
    return path


# ── 第三个夹具：脚注 + 表格（M2；真实样书里没有，故按 EPUB3 标准写法自造）──
NOTES_OPF = """<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>注解之书</dc:title><dc:creator>某作者</dc:creator>
    <dc:language>ja</dc:language><dc:identifier id="bookid">test-003</dc:identifier>
  </metadata>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="css" href="style.css" media-type="text/css"/>
    <item id="ch1" href="ch1.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine><itemref idref="ch1"/></spine>
</package>
"""

NOTES_NAV = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
<head><title>目次</title></head>
<body><nav epub:type="toc"><ol>
  <li><a href="ch1.xhtml#toc-001">第一章 『表と注』</a></li>
</ol></nav></body></html>
"""

NOTES_CH = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
<head><title>ch1</title></head>
<body><div class="main">
  <p class="bold mfont font-110per" id="toc-001">第一章 『表と注』</p>
  <p>本文の段落で、脚注<a epub:type="noteref" href="#fn1" id="ref1">※1</a>があります。</p>
  <table>
    <caption>能力値の比較</caption>
    <tr><th>名前</th><th>値</th></tr>
    <tr><td>アル</td><td>10</td></tr>
    <tr><td colspan="2">備考欄</td></tr>
  </table>
  <aside epub:type="footnote" id="fn1"><p>これは脚注の本文です。</p></aside>
  <p>脚注のあとの段落。</p>
</div></body></html>
"""


@pytest.fixture
def notes_epub(tmp_path: Path) -> Path:
    """含 EPUB3 标准脚注（`<aside epub:type="footnote">`）与表格的最小 EPUB。"""
    path = tmp_path / "notes.epub"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("mimetype", "application/epub+zip", zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml", CONTAINER)
        z.writestr("item/standard.opf", NOTES_OPF)
        z.writestr("item/nav.xhtml", NOTES_NAV)
        z.writestr("item/ch1.xhtml", NOTES_CH)
        z.writestr("item/style.css", CSS)
    return path
