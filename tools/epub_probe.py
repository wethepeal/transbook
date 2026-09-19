#!/usr/bin/env python3
"""EPUB 结构探测（只用标准库）：为 M1 方案验证 + 规模/成本估算提供实测数据。"""
import sys, os, re, zipfile, posixpath
from xml.etree import ElementTree as ET
from html.parser import HTMLParser

NS = {
    "c": "urn:oasis:names:tc:opendocument:xmlns:container",
    "o": "http://www.idpf.org/2007/opf",
    "d": "http://purl.org/dc/elements/1.1/",
    "n": "http://www.daisy.org/z3986/2005/ncx/",
    "x": "http://www.w3.org/1999/xhtml",
}

SKIP_TAGS = {"script", "style", "head", "title"}


class Stats(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.chars = self.kana = self.kanji = self.ascii = self.other = 0
        self.tags = {}
        self.ruby = self.img = self.table = self.p = 0
        self.headings = 0
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        self.tags[tag] = self.tags.get(tag, 0) + 1
        if tag in SKIP_TAGS:
            self._skip += 1
        if tag == "ruby":
            self.ruby += 1
        elif tag == "img":
            self.img += 1
        elif tag == "table":
            self.table += 1
        elif tag == "p":
            self.p += 1
        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self.headings += 1

    def handle_endtag(self, tag):
        if tag in SKIP_TAGS and self._skip > 0:
            self._skip -= 1

    def handle_data(self, data):
        if self._skip:
            return
        for ch in data:
            if ch.isspace():
                continue
            self.chars += 1
            o = ord(ch)
            if 0x3040 <= o <= 0x30FF:
                self.kana += 1
            elif 0x4E00 <= o <= 0x9FFF:
                self.kanji += 1
            elif o < 128:
                self.ascii += 1
            else:
                self.other += 1


def localname(tag):
    return tag.rsplit("}", 1)[-1]


def count_toc(xml_bytes, kind):
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return 0, 0
    if kind == "ncx":
        points = [e for e in root.iter() if localname(e.tag) == "navPoint"]
        depths = [len([a for a in p.iter() if localname(a.tag) == "navPoint"]) for p in points]
        return len(points), max(depths) if depths else 0
    else:  # nav.xhtml
        links = [e for e in root.iter() if localname(e.tag) == "a"]
        return len(links), 1


def probe(path):
    print("=" * 78)
    name = os.path.basename(path)
    print(f"文件: {name}")
    print(f"大小: {os.path.getsize(path)/1024/1024:.2f} MB")
    z = zipfile.ZipFile(path)
    names = z.namelist()
    print(f"ZIP 条目: {len(names)}")

    if "META-INF/encryption.xml" in names:
        print("🔒 DRM: 存在 META-INF/encryption.xml —— 可能受保护")
    else:
        print("DRM: 无 encryption.xml（可正常解析）")

    container = ET.fromstring(z.read("META-INF/container.xml"))
    opf_path = None
    for rf in container.iter():
        if localname(rf.tag) == "rootfile":
            opf_path = rf.get("full-path")
    print(f"OPF: {opf_path}")
    opf = ET.fromstring(z.read(opf_path))
    base = posixpath.dirname(opf_path)

    meta = {}
    for e in opf.iter():
        ln = localname(e.tag)
        if ln in ("title", "creator", "language", "publisher", "identifier", "date"):
            meta.setdefault(ln, (e.text or "").strip())
    print("元数据: " + " | ".join(f"{k}={v[:40]}" for k, v in meta.items()))

    manifest = {}
    for e in opf.iter():
        if localname(e.tag) == "item":
            manifest[e.get("id")] = {
                "href": e.get("href"),
                "type": e.get("media-type") or "",
                "props": e.get("properties") or "",
            }
    spine_ids = [e.get("idref") for e in opf.iter() if localname(e.tag) == "itemref"]

    by_type = {}
    for it in manifest.values():
        by_type[it["type"]] = by_type.get(it["type"], 0) + 1
    print("清单: " + " | ".join(f"{k}={v}" for k, v in sorted(by_type.items())))
    print(f"spine 文档数: {len(spine_ids)}")

    # 目录
    toc_count = toc_depth = 0
    for it in manifest.values():
        if "nav" in it["props"]:
            toc_count, toc_depth = count_toc(z.read(posixpath.join(base, it["href"])), "nav")
            print(f"NAV 目录项: {toc_count}")
            break
    else:
        for it in manifest.values():
            if "dtbncx" in it["type"]:
                toc_count, toc_depth = count_toc(z.read(posixpath.join(base, it["href"])), "ncx")
                print(f"NCX 目录项: {toc_count} (层级≈{toc_depth})")

    # 样式：竖排检测
    vertical = 0
    css_files = [it["href"] for it in manifest.values() if "css" in it["type"]]
    for href in css_files:
        try:
            css = z.read(posixpath.join(base, href)).decode("utf-8", "ignore")
        except KeyError:
            continue
        vertical += len(re.findall(r"vertical-rl|vertical-lr|writing-mode\s*:\s*vertical", css))
    print(f"CSS 文件: {len(css_files)} ｜ 竖排(writing-mode: vertical) 出现次数: {vertical}"
          + ("  ← 竖排排版" if vertical else "  ← 横排"))

    # 正文统计
    total = Stats()
    per_doc = []
    for sid in spine_ids:
        it = manifest.get(sid)
        if not it or "html" not in it["type"]:
            continue
        try:
            raw = z.read(posixpath.join(base, it["href"]))
        except KeyError:
            continue
        s = Stats()
        try:
            s.feed(raw.decode("utf-8", "ignore"))
        except Exception:
            pass
        per_doc.append((it["href"], s))
        for attr in ("chars", "kana", "kanji", "ascii", "other", "ruby", "img", "table", "p", "headings"):
            setattr(total, attr, getattr(total, attr) + getattr(s, attr))

    print("-" * 78)
    print(f"正文字符总数（去空白）: {total.chars:,}")
    print(f"  日文假名 {total.kana:,} ｜ 汉字 {total.kanji:,} ｜ ASCII {total.ascii:,} ｜ 其他 {total.other:,}")
    jp = total.kana + total.kanji
    print(f"  日文字符占比: {jp/max(total.chars,1)*100:.1f}%")
    print(f"段落 <p>: {total.p:,} ｜ 标题 <h1-6>: {total.headings} ｜ 注音 <ruby>: {total.ruby:,}")
    print(f"图片 <img>: {total.img} ｜ 表格 <table>: {total.table}")
    img_bytes = sum(z.getinfo(n).file_size for n in names
                    if re.search(r"\.(jpe?g|png|gif|webp)$", n, re.I))
    font_bytes = sum(z.getinfo(n).file_size for n in names
                     if re.search(r"\.(otf|ttf|woff2?)$", n, re.I))
    print(f"图片总大小: {img_bytes/1024/1024:.2f} MB ｜ 内嵌字体: {font_bytes/1024/1024:.2f} MB")
    if per_doc:
        avg = sum(s.chars for _, s in per_doc) / len(per_doc)
        print(f"文档平均字符数: {avg:,.0f}（最大 {max(s.chars for _, s in per_doc):,}）")
    # token 粗估（CJK 约 1~2 字符/token）
    print(f"→ 输入 token 粗估: {jp/2:,.0f} ~ {jp/1:,.0f}")
    return {"chars": total.chars, "jp": jp, "p": total.p, "img": total.img,
            "size_mb": os.path.getsize(path)/1024/1024, "vertical": vertical,
            "toc": toc_count, "docs": len(per_doc)}


if __name__ == "__main__":
    results = []
    for p in sys.argv[1:]:
        try:
            results.append(probe(p))
        except Exception as e:
            print(f"!! 解析失败: {p}\n   {type(e).__name__}: {e}")
    if len(results) > 1:
        print("=" * 78)
        print("汇总:")
        print(f"  合计正文字符: {sum(r['chars'] for r in results):,}")
        print(f"  合计日文字符: {sum(r['jp'] for r in results):,}")
        print(f"  合计段落数  : {sum(r['p'] for r in results):,}")
        print(f"  输入 token 粗估: {sum(r['jp'] for r in results)/2:,.0f} ~ {sum(r['jp'] for r in results)/1:,.0f}")
