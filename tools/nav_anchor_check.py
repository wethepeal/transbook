#!/usr/bin/env python3
"""NAV 锚点解析验证（M0 实测 #2 的量化结论）。

算法假设（来自样书调研）：
    目录(nav/NCX) 给出 (标题, 文件#fragment) → 正文里存在 id=fragment 的元素即章节标题块。

本脚本对整本书逐条验证，输出通过率与不匹配明细，用来证明结构还原可以做到"零启发式"。

用法：python tools/nav_anchor_check.py <book.epub> [...]
"""

from __future__ import annotations

import posixpath
import re
import sys
import zipfile
from xml.etree import ElementTree as ET

from lxml import html as LH

NS = {
    "c": "urn:oasis:names:tc:opendocument:xmlns:container",
    "o": "http://www.idpf.org/2007/opf",
    "n": "http://www.daisy.org/z3986/2005/ncx/",
}


def local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def norm(s: str) -> str:
    """归一化标题，便于比较（去空白、全角空格、书名号差异）。"""
    return re.sub(r"[\s\u3000]+", "", s or "")


def text_no_rt(el) -> str:
    """取元素文本但跳过 <rt>（注音）子树。

    日文电子书的标题/正文里大量使用 <ruby>基文<rt>注音</rt></ruby>，
    甚至逐字注音（<ruby>幕<rt>まく</rt>間<rt>あい</rt></ruby>）。
    不剥离 rt 会把假名混进正文并与 NAV 标题不一致。
    """
    parts: list[str] = []

    def walk(node) -> None:
        if not isinstance(node.tag, str):  # 注释 / 处理指令
            return
        if local(node.tag) == "rt":
            return
        if node.text:
            parts.append(node.text)
        for child in node:
            walk(child)
            if child.tail:
                parts.append(child.tail)

    walk(el)
    return re.sub(r"[\s\u3000]+", "", "".join(parts))


def describe(el) -> str:
    cls = (el.get("class") or "").strip().replace(" ", ".")
    out = local(el.tag)
    if cls:
        out += "." + cls
    if el.get("id"):
        out += "#" + el.get("id")
    return out


def load(z: zipfile.ZipFile):
    container = ET.fromstring(z.read("META-INF/container.xml"))
    opf_path = next(rf.get("full-path") for rf in container.iter() if local(rf.tag) == "rootfile")
    opf = ET.fromstring(z.read(opf_path))
    base = posixpath.dirname(opf_path)
    manifest = {
        e.get("id"): {"href": e.get("href") or "", "type": e.get("media-type") or "",
                      "props": e.get("properties") or ""}
        for e in opf.iter() if local(e.tag) == "item"
    }
    return base, manifest


def nav_entries(z, base, manifest):
    for it in manifest.values():
        if "nav" in it["props"]:
            doc = LH.fromstring(z.read(posixpath.join(base, it["href"])))
            navs = doc.xpath("//*[local-name()='nav' and @*[local-name()='type']='toc']") or \
                   doc.xpath("//*[local-name()='nav']")
            out = []
            if navs:
                for li in navs[0].xpath(".//*[local-name()='li']"):
                    a = li.xpath("./*[local-name()='a']")
                    if not a:
                        continue
                    level = len(li.xpath("ancestor::*[local-name()='ol']"))
                    out.append((level, re.sub(r"\s+", " ", a[0].text_content()).strip(),
                                a[0].get("href") or ""))
            return out, "nav"
    for it in manifest.values():
        if "dtbncx" in it["type"]:
            root = ET.fromstring(z.read(posixpath.join(base, it["href"])))
            out = []
            for p in (e for e in root.iter() if local(e.tag) == "navPoint"):
                level = len([a for a in p.iter("{%s}navPoint" % NS["n"])])
                label = p.find(".//{%s}text" % NS["n"])
                content = p.find("{%s}content" % NS["n"])
                out.append((level, (label.text or "").strip() if label is not None else "",
                            content.get("src") if content is not None else ""))
            return out, "ncx"
    return [], "none"


def check(path: str) -> dict:
    z = zipfile.ZipFile(path)
    base, manifest = load(z)
    entries, kind = nav_entries(z, base, manifest)
    print("=" * 84)
    print(f"样书: {posixpath.basename(path)}")
    print(f"目录来源: {kind} ｜ 条目: {len(entries)}")

    cache: dict[str, object] = {}
    ok = miss_id = miss_file = title_mismatch = no_fragment = 0
    rows = []
    for level, title, href in entries:
        if "#" not in href:
            no_fragment += 1
            rows.append(("—", "无锚点", title, href, ""))
            continue
        file_part, frag = href.split("#", 1)
        inner = posixpath.normpath(posixpath.join(base, file_part))
        if inner not in cache:
            try:
                cache[inner] = LH.fromstring(z.read(inner))
            except KeyError:
                cache[inner] = None
        root = cache[inner]
        if root is None:
            miss_file += 1
            rows.append(("✗", "文件缺失", title, href, ""))
            continue
        found = root.xpath(f"//*[@id='{frag}']")
        if not found:
            miss_id += 1
            rows.append(("✗", "锚点未找到", title, href, ""))
            continue
        el = found[0]
        body_text = text_no_rt(el)
        same = norm(title) in body_text or body_text in norm(title)
        if same:
            ok += 1
        else:
            title_mismatch += 1
        rows.append(("✓" if same else "~", describe(el), title, href, body_text[:46]))

    print("\n逐条验证（✓ 完全匹配 / ~ 文本不一致 / ✗ 失败）:")
    for mark, dsc, title, href, body in rows:
        print(f"  {mark} {dsc:<34} NAV={title[:26]:<28} 正文={body!r}")

    total = len(entries)
    print(f"\n结果: 完全匹配 {ok}/{total} ｜ 文本不一致 {title_mismatch} ｜ 锚点缺失 {miss_id} "
          f"｜ 文件缺失 {miss_file} ｜ 无锚点 {no_fragment}")
    hit = ok + title_mismatch
    print(f"→ 结构定位成功率（能按锚点拿到标题块）: {hit}/{total} = {hit / max(total,1) * 100:.0f}%")
    return {"total": total, "ok": ok, "hit": hit}


if __name__ == "__main__":
    stats = [check(p) for p in sys.argv[1:]]
    if len(stats) > 1:
        t = sum(s["total"] for s in stats)
        h = sum(s["hit"] for s in stats)
        o = sum(s["ok"] for s in stats)
        print("=" * 84)
        print(f"合计: 定位成功 {h}/{t} = {h/max(t,1)*100:.0f}% ｜ 标题完全匹配 {o}/{t} = {o/max(t,1)*100:.0f}%")
