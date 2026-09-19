#!/usr/bin/env python3
"""NAV/类名结构识别原型（M0 实测 #2）。

目的：证明在不依赖 <h1>~<h6> 的前提下，能正确还原电子书的章节结构。
样书实测已知：该出版社 EPUB 的标题语义标签计数为 0，必须靠 NAV/NCX + 类名启发式。

输出三部分报告：
  A. 目录（NAV / NCX）结构与它指向的 spine 文档
  B. 每篇文档的「标签.类名」频次画像（找出正文与标题的真实标记方式）
  C. 首篇正文的标记骨架 + 标题候选（类名/字号启发式）

用法：python tools/nav_probe.py <book.epub> [更多.epub ...]
"""

from __future__ import annotations

import posixpath
import re
import sys
import zipfile
from collections import Counter
from xml.etree import ElementTree as ET

from lxml import html as LH

NS = {
    "c": "urn:oasis:names:tc:opendocument:xmlns:container",
    "o": "http://www.idpf.org/2007/opf",
    "n": "http://www.daisy.org/z3986/2005/ncx/",
}

# 标题类名启发式（日文电子书常见写法一并覆盖）
HEAD_HINT = re.compile(
    r"title|heading|head|chapter|chap|section|midashi|mokuji|table[_-]?of|"
    r"h[1-6]|caption|subtitle|ttl|daimei|oasaka|level",
    re.I,
)
# 正文段落类名启发式
BODY_HINT = re.compile(r"text|body|honbun|paragraph|para|p\d*$", re.I)


def local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def describe(el) -> str:
    cls = (el.get("class") or "").strip().replace(" ", ".")
    ident = el.get("id") or ""
    out = el.tag
    if cls:
        out += "." + cls
    if ident:
        out += "#" + ident
    return out


def text_of(el) -> str:
    return re.sub(r"\s+", " ", el.text_content()).strip()


def load_opf(z: zipfile.ZipFile):
    container = ET.fromstring(z.read("META-INF/container.xml"))
    opf_path = next(
        rf.get("full-path") for rf in container.iter() if local(rf.tag) == "rootfile"
    )
    opf = ET.fromstring(z.read(opf_path))
    base = posixpath.dirname(opf_path)
    manifest: dict[str, dict[str, str]] = {}
    for e in opf.iter():
        if local(e.tag) == "item":
            manifest[e.get("id")] = {
                "href": e.get("href") or "",
                "type": e.get("media-type") or "",
                "props": e.get("properties") or "",
            }
    spine = [e.get("idref") for e in opf.iter() if local(e.tag) == "itemref"]
    return base, manifest, spine


def parse_nav(z, base, manifest):
    """优先 EPUB3 nav，回退 NCX；返回 [(level, title, href)]。"""
    for it in manifest.values():
        if "nav" in it["props"]:
            doc = LH.fromstring(z.read(posixpath.join(base, it["href"])))
            nav = doc.xpath("//*[local-name()='nav' and @*[local-name()='type']='toc']")
            if not nav:
                nav = doc.xpath("//*[local-name()='nav']")
            entries = []
            if nav:
                for li in nav[0].xpath(".//*[local-name()='li']"):
                    a = li.xpath("./*[local-name()='a' or local-name()='span']")
                    if not a:
                        continue
                    level = len(li.xpath("ancestor::*[local-name()='ol']"))
                    entries.append((level, text_of(a[0]), a[0].get("href") or ""))
            return entries, "nav"
    for it in manifest.values():
        if "dtbncx" in it["type"]:
            root = ET.fromstring(z.read(posixpath.join(base, it["href"])))
            entries = []
            for p in (e for e in root.iter() if local(e.tag) == "navPoint"):
                level = len([a for a in p.iter("{%s}navPoint" % NS["n"])])
                label = p.find(".//{%s}text" % NS["n"])
                content = p.find("{%s}content" % NS["n"])
                entries.append(
                    (level, (label.text or "").strip() if label is not None else "",
                     content.get("src") if content is not None else "")
                )
            return entries, "ncx"
    return [], "none"


def css_font_sizes(z, base, manifest) -> set[str]:
    """收集 CSS 里出现过的字号，用于判断「更大字号 = 标题」这条启发式是否可用。"""
    sizes = set()
    for it in manifest.values():
        if "css" not in it["type"]:
            continue
        try:
            css = z.read(posixpath.join(base, it["href"])).decode("utf-8", "ignore")
        except KeyError:
            continue
        sizes.update(re.findall(r"font-size\s*:\s*([^;}\n]+)", css, re.I))
    return sizes


def probe(path: str) -> None:
    print("=" * 80)
    print(f"样书: {posixpath.basename(path)}")
    z = zipfile.ZipFile(path)
    base, manifest, spine = load_opf(z)
    docs = [manifest[s] for s in spine if s in manifest and "html" in manifest[s]["type"]]

    # ── A. 目录 ────────────────────────────────────────────
    entries, kind = parse_nav(z, base, manifest)
    print(f"\n【A. 目录】来源={kind}，条目 {len(entries)} 条")
    for level, title, href in entries[:12]:
        print(f"   {'  ' * max(level - 1, 0)}L{level} {title[:34]:<36} -> {href[:42]}")
    if len(entries) > 12:
        print(f"   ... 其余 {len(entries) - 12} 条略")

    # ── B. 标记画像 ────────────────────────────────────────
    print(f"\n【B. 标记画像】spine 文档 {len(docs)} 篇")
    shape = Counter()
    per_doc = []
    for d in docs:
        try:
            root = LH.fromstring(z.read(posixpath.join(base, d["href"])))
        except Exception:
            continue
        c = Counter(describe(e) for e in root.iter() if isinstance(e.tag, str))
        shape.update(c)
        per_doc.append((d["href"], root, c))
    print("   全书画像 Top 14（标签.类名 → 次数）:")
    for name, n in shape.most_common(14):
        print(f"     {name:<44} {n:>6}")
    print(f"\n   CSS 中出现的字号: {sorted(css_font_sizes(z, base, manifest))[:12]}")

    # ── C. 标题候选 ────────────────────────────────────────
    print(f"\n【C. 首篇正文骨架 + 标题候选】{per_doc[0][0] if per_doc else '—'}")
    if per_doc:
        _, root, _ = per_doc[0]
        body = root.find("body")
        if body is not None:
            for i, el in enumerate(list(body)[:16]):
                t = text_of(el)
                print(f"     [{i:02d}] {describe(el):<40} {t[:52]!r}")
        print("\n   标题候选（类名/字号/短文本启发式）:")
        cands = []
        for el in root.iter():
            if not isinstance(el.tag, str) or el.tag in ("html", "body", "head"):
                continue
            cls = el.get("class") or ""
            t = text_of(el)
            if not t:
                continue
            score = 0
            reasons = []
            if HEAD_HINT.search(cls):
                score += 2
                reasons.append("类名")
            if el.tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
                score += 3
                reasons.append("语义标签")
            style = el.get("style") or ""
            if re.search(r"font-size\s*:\s*(1\.[3-9]|[2-9])", style):
                score += 2
                reasons.append("行内大字")
            if len(t) <= 30 and len(list(el)) == 0:
                score += 1
                reasons.append("短文本")
            if score >= 2:
                cands.append((score, describe(el), t[:40], "+".join(reasons)))
        for sc, dsc, t, why in sorted(cands, key=lambda x: -x[0])[:12]:
            print(f"     score={sc} {dsc:<38} {t!r:<44} [{why}]")
        if not cands:
            print("     ⚠️ 未找到任何标题候选——需要改用 NAV 锚点 + 段落位置策略")


if __name__ == "__main__":
    for p in sys.argv[1:]:
        probe(p)
