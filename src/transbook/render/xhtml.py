"""IR + 译文 → XHTML（EPUB 的内容来源）。

两种模式（对应计划书 §4.6 与你的 Q5 要求）：
* `bilingual`：段落级对照（原文在上、译文在下），**供审核**
* `zh`：只输出译文，**终版**

按章节切分文件：遇到 `heading` 块就开一个新文件；文件内的块按原文顺序输出。
图片引用 `images/` 目录（打包时原样带入）。
"""

from __future__ import annotations

import posixpath
from dataclasses import dataclass, field
from typing import Literal

from lxml import etree

from transbook.ir import DocumentIR

XHTML_NS = "http://www.w3.org/1999/xhtml"
XML_NS = "http://www.w3.org/XML/1998/namespace"
#: EPUB3 的结构语义命名空间（`epub:type`）
OPS_NS = "http://www.idpf.org/2007/ops"
Mode = Literal["bilingual", "zh"]

CSS = """\
@charset "utf-8";
body { font-family: "Noto Serif SC", "Source Han Serif SC", serif; line-height: 1.8;
       text-align: justify; margin: 0 5%; }
h1, h2, h3, h4 { font-family: "Noto Sans SC", "Source Han Sans SC", sans-serif;
       line-height: 1.4; margin: 1.4em 0 .8em; page-break-after: avoid; }
h1 { font-size: 1.5em; } h2 { font-size: 1.3em; } h3 { font-size: 1.15em; }
p { margin: 0 0 .55em; text-indent: 2em; }
p.no-indent { text-indent: 0; }
.bi { margin: 0 0 .9em; }
.bi-src { color: #666; font-size: .88em; text-indent: 2em; margin: 0 0 .15em; }
.bi-tgt { text-indent: 2em; margin: 0; }
figure { margin: 1.2em 0; text-align: center; page-break-inside: avoid; }
img { max-width: 100%; height: auto; }
figcaption { font-size: .85em; color: #666; margin-top: .4em; }
nav[epub|type~="toc"] ol { list-style: none; padding-left: 1em; }
table.tbl { border-collapse: collapse; margin: 1em 0; font-size: .92em; width: 100%; }
table.tbl td, table.tbl th { border: 1px solid #bbb; padding: .3em .5em; text-indent: 0; }
table.tbl caption { font-size: .88em; color: #666; margin-bottom: .3em; }
aside.footnote { font-size: .85em; color: #555; margin: .6em 0; padding-left: 1em;
       border-left: 2px solid #ddd; text-indent: 0; }
sup a.noteref { text-decoration: none; font-size: .75em; vertical-align: super; }
/* 封面页：整页居中、不留边距，避免阅读器把它排成正文页 */
body[epub|type~="cover"] { margin: 0; padding: 0; text-align: center; }
.cover { margin: 0; padding: 0; text-align: center; page-break-after: always; }
.cover img { max-width: 100%; max-height: 100%; }
"""


@dataclass
class Chapter:
    """一个 EPUB 内容文档。"""

    id: str
    title: str
    filename: str          # 相对 OEBPS，如 text/ch0001.xhtml
    content: bytes
    level: int = 1
    blocks: list[str] = field(default_factory=list)


def _el(tag: str, **attrs: str) -> etree._Element:
    e = etree.Element(f"{{{XHTML_NS}}}{tag}")
    for k, v in attrs.items():
        name = k.replace("_", "-")
        # lxml 要求命名空间属性用 {uri}local 形式，不能写字面量 "xml:lang"
        if name == "xml:lang":
            name = f"{{{XML_NS}}}lang"
        e.set(name, v)
    return e


def _append_text(parent: etree._Element, tag: str, text: str, **attrs: str) -> etree._Element:
    child = _el(tag, **attrs)
    child.text = text
    parent.append(child)
    return child


def _html_doc(lang: str, title: str, body_children: list[etree._Element],
              *, body_type: str | None = None,
              css_href: str = "../style.css") -> bytes:
    """构造一个 XHTML 文档。

    `css_href` 必须随文档位置而变：正文在 `OEBPS/text/` 下用 `../style.css`，
    而 `nav.xhtml` 就在 `OEBPS/` 下、只能用 `style.css`——写错会指向包外，
    校验器会报"引用了不存在的文件"（内置校验实测抓到过）。
    """
    # 用默认命名空间（nsmap 的 None 键），否则会序列化成 <html:html> 前缀形式
    nsmap: dict[str | None, str] = {None: XHTML_NS}
    if body_type:
        # `epub:type` 需要声明 epub 前缀，否则 lxml 会生成 ns0 这样的一次性前缀
        nsmap["epub"] = OPS_NS
    root = etree.Element(f"{{{XHTML_NS}}}html", nsmap=nsmap)
    root.set(f"{{{XML_NS}}}lang", lang)
    root.set("lang", lang)
    head = _el("head")
    _append_text(head, "title", title)
    link = _el("link", rel="stylesheet", type="text/css", href=css_href)
    head.append(link)
    body = _el("body")
    if body_type:
        body.set(f"{{{OPS_NS}}}type", body_type)
    for c in body_children:
        body.append(c)
    root.append(head)
    root.append(body)
    return etree.tostring(root, xml_declaration=True, encoding="utf-8",
                          doctype='<!DOCTYPE html>')


def build_cover(title: str, image_href: str) -> bytes:
    """EPUB3 封面页（M4）。

    单独一个 XHTML 整页显示封面图——多数阅读器的书架缩略图取的就是这一页；
    清单里那张图还要带 `properties="cover-image"`（在打包器 `_opf` 里加）。

    不用 `<svg><image>` 那套写法：它要额外读图片尺寸，而收益只在极端宽高比时
    才看得出来；`<img>` + CSS 已能覆盖主流阅读器。
    """
    div = _el("div", **{"class": "cover"})
    div.append(_el("img", src=image_href, alt=title or "cover"))
    return _html_doc("zh", title or "封面", [div], body_type="cover")


def _with_noterefs(p: etree._Element, refs: list[str]) -> etree._Element:
    """在段落末尾补上脚注跳转上标（`[1]`），指向 `<aside id="…">`。"""
    for i, ref in enumerate(refs, start=1):
        sup = _el("sup")
        a = _el("a", href=f"#{ref}", **{"class": "noteref"})
        a.text = f"[{i}]"
        sup.append(a)
        p.append(sup)
    return p


def build_chapters(ir: DocumentIR, translations: dict[str, str], mode: Mode = "bilingual",
                   lang_src: str | None = None, lang_tgt: str = "zh") -> list[Chapter]:
    """把 IR 切成章节 XHTML。

    `translations` 以 **block_id** 为键（调用方从段落表按 block_id 映射）。
    """
    lang_src = lang_src or ir.doc.source_lang or "ja"
    chapters: list[Chapter] = []
    cur: list[etree._Element] = []
    cur_title = ir.doc.title or "正文"
    cur_level = 1
    seq = 0

    def flush() -> None:
        nonlocal cur, seq
        if not cur:
            return
        seq += 1
        chapters.append(Chapter(
            id=f"ch{seq:04d}",
            title=cur_title,
            filename=f"text/ch{seq:04d}.xhtml",
            content=_html_doc(lang_tgt, cur_title, cur),
        ))
        cur = []

    for b in ir.blocks:
        if b.type == "heading":
            flush()
            cur_title = b.text or cur_title
            cur_level = b.level or 1
            cur.append(_append_text(_el("div", **{"class": "chapter"}), f"h{min(cur_level, 4)}",
                                    b.text or ""))
            continue
        if b.type == "image":
            fig = _el("figure")
            # 统一放到 images/<basename>，与打包器写入位置一致
            img = _el("img", src=f"../images/{posixpath.basename(b.path or '')}",
                      alt=b.caption or "")
            fig.append(img)
            if b.caption:
                _append_text(fig, "figcaption", b.caption)
            cur.append(fig)
            continue
        if b.type == "table":
            # 表格按**单元格**取译文（unit id = `{block_id}:r{r}c{c}`），结构照原样重建
            tbl = _el("table", **{"class": "tbl"})
            if b.caption:
                _append_text(tbl, "caption", b.caption)
            for r, row in enumerate(b.rows):
                tr = _el("tr")
                for c, cell in enumerate(row):
                    tgt = translations.get(f"{b.id}:r{r}c{c}", "").strip()
                    td = _el("td")
                    if mode == "zh":
                        td.text = tgt or cell  # 缺译也保留原文，避免表格错位
                    else:
                        _append_text(td, "span", cell, **{"class": "bi-src", "xml:lang": lang_src})
                        if tgt:
                            _append_text(td, "span", tgt, **{"class": "bi-tgt",
                                                             "xml:lang": lang_tgt})
                    if r < len(b.cell_spans) and c < len(b.cell_spans[r]):
                        rs, cs = b.cell_spans[r][c]
                        if rs > 1:
                            td.set("rowspan", str(rs))
                        if cs > 1:
                            td.set("colspan", str(cs))
                    tr.append(td)
                tbl.append(tr)
            cur.append(tbl)
            continue
        if b.type == "footnote":
            tgt = translations.get(b.id, "").strip()
            aside = _el("aside", **{"class": "footnote", "role": "doc-footnote"})
            if b.note_id:
                aside.set("id", b.note_id)
            if mode == "zh":
                if not tgt:
                    continue
                aside.text = tgt
            else:
                _append_text(aside, "p", b.text, **{"class": "bi-src", "xml:lang": lang_src})
                if tgt:
                    _append_text(aside, "p", tgt, **{"class": "bi-tgt", "xml:lang": lang_tgt})
            cur.append(aside)
            continue
        if b.type not in ("paragraph", "footnote"):
            continue
        tgt = translations.get(b.id, "").strip()
        if mode == "zh":
            if tgt:
                cur.append(_with_noterefs(_append_text(_el("p"), "p", tgt), b.refs))
            continue
        # 双语：原文 + 译文成对
        wrap = _el("div", **{"class": "bi"})
        _append_text(wrap, "p", b.text, **{"class": "bi-src", "xml:lang": lang_src})
        if tgt:
            _append_text(wrap, "p", tgt, **{"class": "bi-tgt", "xml:lang": lang_tgt})
        cur.append(wrap)
    flush()
    return chapters


def build_nav(chapters: list[Chapter]) -> bytes:
    """生成 EPUB3 nav.xhtml（目录）。"""
    nav = _el("nav", id="toc")
    nav.set("{http://www.idpf.org/2007/ops}type", "toc")
    _append_text(nav, "h1", "目录")
    ol = _el("ol")
    for ch in chapters:
        li = _el("li")
        a = _el("a", href=ch.filename)
        a.text = ch.title
        li.append(a)
        ol.append(li)
    nav.append(ol)
    # nav.xhtml 就在 OEBPS/ 下，样式表是同级的 style.css（写成 ../ 会指到包外）
    return _html_doc("zh", "目录", [nav], css_href="style.css")
