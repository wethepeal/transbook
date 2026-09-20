#!/usr/bin/env python3
"""把 EPUB 内某个 XHTML 的标记骨架打印出来（M0 结构识别调研用）。

用法：python tools/dump_xhtml.py <book.epub> <内部路径如 xhtml/p-003.xhtml> [最多元素数=60]
"""

from __future__ import annotations

import re
import sys
import zipfile
from collections import Counter

from lxml import html as LH


def local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def describe(el) -> str:
    cls = (el.get("class") or "").strip().replace(" ", ".")
    out = local(el.tag) if isinstance(el.tag, str) else str(el.tag)
    if cls:
        out += "." + cls
    if el.get("id"):
        out += "#" + el.get("id")
    for attr in ("epub:type", "role", "style"):
        v = el.get(attr)
        if v:
            out += f" [{attr}={v[:40]}]"
    return out


def text_of(el) -> str:
    return re.sub(r"\s+", " ", el.text_content()).strip()


def main() -> None:
    epub, inner = sys.argv[1], sys.argv[2]
    limit = int(sys.argv[3]) if len(sys.argv) > 3 else 60
    z = zipfile.ZipFile(epub)
    raw = z.read(inner)
    root = LH.fromstring(raw)
    print(f"文件: {inner}  ({len(raw)/1024:.1f} KB)")

    classes = Counter()
    for el in root.iter():
        if isinstance(el.tag, str) and el.get("class"):
            classes[el.get("class")] += 1
    print(f"\n本文件类名清单（{len(classes)} 种）:")
    for name, n in classes.most_common(20):
        print(f"   {name:<52} {n:>5}")

    print(f"\n标记骨架（前 {limit} 个元素，缩进=层级）:")
    count = 0
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        if local(el.tag) in ("html", "head", "meta", "link", "title", "script", "style"):
            continue
        depth = len(el.xpath("ancestor::*"))
        t = text_of(el)
        line = f"{'  ' * min(depth, 12)}{describe(el)}"
        if t:
            line += f"  ▸ {t[:60]!r}"
        print(line)
        count += 1
        if count >= limit:
            break

    print("\n锚点元素（toc-* / 章节起点）:")
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        if el.get("id") and re.search(r"toc|chapter|title|head", el.get("id"), re.I):
            print(f"   {describe(el):<46} ▸ {text_of(el)[:50]!r}")
            # 打印它的父与后续兄弟，判断标题是否紧跟其后
            parent = el.getparent()
            if parent is not None:
                sibs = list(parent)
                idx = sibs.index(el)
                for s in sibs[idx + 1: idx + 4]:
                    if isinstance(s.tag, str):
                        print(f"        兄弟→ {describe(s):<40} ▸ {text_of(s)[:50]!r}")

    print("\n正文段落样本（前 6 个 p 及其类名）:")
    ps = [e for e in root.iter() if isinstance(e.tag, str) and local(e.tag) == "p"]
    for i, el in enumerate(ps[:6]):
        print(f"   [{i}] {describe(el):<42} ▸ {text_of(el)[:58]!r}")


if __name__ == "__main__":
    main()
