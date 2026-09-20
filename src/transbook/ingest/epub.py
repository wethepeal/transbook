"""EPUB → DocumentIR 抽取器。

算法来自 M0 实测（见 docs/m0-report.md §2），两条关键结论驱动了实现：

1. **结构取自 NAV 锚点**：目录给出 `(标题, 文件#fragment)`，正文里 `id=fragment` 的元素即章节标题。
   实测两本样书 **16/16 命中、标题文本 16/16 完全一致**——不需要字号统计或 ML 版面模型。
2. **必须剥离 `<rt>` 注音**：日文电子书常用逐字注音，不剥离会导致标题与目录对不上、假名混进正文。

M1 范围：标题 / 段落 / 图片；脚注与表格留到 M2（此处只统计数量）。
"""

from __future__ import annotations

import posixpath
import re
import zipfile
from pathlib import Path
from typing import Any

from lxml import etree

from transbook.ir import Block, DocMeta, DocumentIR, InlineSpan, TocEntry
from transbook.textutil import classify_matter, count_rt, is_blank_block, localname, text_without_rt

XHTML_TYPES = ("application/xhtml+xml", "text/html")
XLINK_NS = "http://www.w3.org/1999/xlink"
EPUB_TYPE_NS = "http://www.idpf.org/2007/ops"
#: 脚注判定线索（EPUB3 标准写法 + 常见阅读器/制作工具的类名）
_NOTE_HINT = re.compile(r"footnote|endnote|doc-footnote|注釈|脚注|註", re.I)


def _is_footnote(el: Any) -> bool:
    """该元素是否是脚注容器（`<aside epub:type="footnote">` 及其变体）。"""
    if localname(el.tag) not in ("aside", "div", "section"):
        return False
    return bool(_NOTE_HINT.search(el.get(f"{{{EPUB_TYPE_NS}}}type") or "")
                or _NOTE_HINT.search(el.get("role") or "")
                or _NOTE_HINT.search(el.get("class") or ""))
_IMG_EXT = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg")
_VERTICAL = re.compile(r"writing-mode\s*:\s*vertical|vertical-rl|vertical-lr", re.I)
_HEAD_CLASS = re.compile(r"bold|mfont|font-1[0-9]{2}per|title|heading|midashi", re.I)
_SLUG_KEEP = re.compile(r"[^0-9A-Za-z\u3040-\u30ff\u4e00-\u9fff]+")


def slugify(text: str, maxlen: int = 28) -> str:
    """把书名/文件名压成可读且短的 ID（会进入 seg_id，故必须简洁）。

    例：`Re：ゼロから始める異世界生活 43 (MF文庫J)` → `Re-ゼロから始める異世界生活-43`
    """
    s = _SLUG_KEEP.sub("-", (text or "").strip()).strip("-")
    return (s[:maxlen].rstrip("-") or "book")


class EpubError(RuntimeError):
    """EPUB 结构异常。"""


def _norm(base: str, href: str) -> str:
    """把相对 href 解析成 zip 内绝对路径。"""
    return posixpath.normpath(posixpath.join(base, href))


def _itertext(el: Any) -> str:
    """etree 元素取全部文本（etree 没有 text_content，那是 lxml.html 的方法）。"""
    return re.sub(r"[\s\u3000]+", " ", "".join(el.itertext())).strip()


def _read_xml(z: zipfile.ZipFile, name: str) -> Any:
    try:
        raw = z.read(name)
    except KeyError as exc:
        raise EpubError(f"EPUB 内缺少文件: {name}") from exc
    parser = etree.XMLParser(recover=True, resolve_entities=False, no_network=True)
    return etree.fromstring(raw, parser=parser)


def _spans_of(el: Any) -> list[InlineSpan]:
    """收集行内语义片段（ruby 基文 / 縦中横 / 强调）。M1 只记录，不参与翻译。"""
    spans: list[InlineSpan] = []
    for node in el.iter():
        if not isinstance(node.tag, str):
            continue
        tag = localname(node.tag)
        cls = node.get("class") or ""
        if tag == "ruby":
            base = text_without_rt(node)
            if base:
                spans.append(InlineSpan(kind="ruby", text=base))
        elif tag == "span" and "tcy" in cls:
            t = text_without_rt(node)
            if t:
                spans.append(InlineSpan(kind="tcy", text=t))
        elif tag in ("em", "i"):
            t = text_without_rt(node)
            if t:
                spans.append(InlineSpan(kind="emphasis", text=t))
        elif tag in ("strong", "b"):
            t = text_without_rt(node)
            if t:
                spans.append(InlineSpan(kind="strong", text=t))
    return spans


def _noterefs(el: Any) -> list[str]:
    """收集块内的脚注引用锚点（`<a epub:type="noteref" href="#fn1">` → `fn1`）。"""
    out: list[str] = []
    for a in el.iter():
        if not isinstance(a.tag, str) or localname(a.tag) != "a":
            continue
        href = a.get("href") or ""
        if href.startswith("#") and len(href) > 1:
            out.append(href[1:])
    return out


class EpubIngestor:
    """把一个 EPUB 文件解析成 DocumentIR。"""

    def __init__(self, path: str | Path, assets_subdir: str = "assets",
                 doc_id: str | None = None) -> None:
        self.path = Path(path)
        self.assets_subdir = assets_subdir
        self.doc_id = doc_id
        self._block_seq = 0
        self._ruby_dropped = 0
        self._images: dict[str, Path] = {}

    # ── 基础解析 ────────────────────────────────────────────────
    def _open(self) -> zipfile.ZipFile:
        if not self.path.is_file():
            raise EpubError(f"文件不存在: {self.path}")
        try:
            return zipfile.ZipFile(self.path)
        except zipfile.BadZipFile as exc:
            raise EpubError(f"不是合法的 EPUB（zip 解析失败）: {exc}") from exc

    def _opf(self, z: zipfile.ZipFile) -> tuple[str, Any]:
        if "META-INF/encryption.xml" in z.namelist():
            raise EpubError("该 EPUB 含 META-INF/encryption.xml（可能受 DRM 保护），无法处理")
        container = _read_xml(z, "META-INF/container.xml")
        opf_path = next(
            (rf.get("full-path") for rf in container.iter() if localname(rf.tag) == "rootfile"),
            None,
        )
        if not opf_path:
            raise EpubError("container.xml 中没有 rootfile")
        return opf_path, _read_xml(z, opf_path)

    @staticmethod
    def _manifest(opf: Any) -> dict[str, dict[str, str]]:
        out: dict[str, dict[str, str]] = {}
        for e in opf.iter():
            if localname(e.tag) == "item":
                out[e.get("id")] = {
                    "href": e.get("href") or "",
                    "type": e.get("media-type") or "",
                    "props": e.get("properties") or "",
                }
        return out

    @staticmethod
    def _spine(opf: Any) -> list[str]:
        return [e.get("idref") for e in opf.iter() if localname(e.tag) == "itemref"]

    @staticmethod
    def _meta(opf: Any) -> dict[str, str]:
        want = {"title", "creator", "language", "publisher", "identifier"}
        out: dict[str, str] = {}
        for e in opf.iter():
            ln = localname(e.tag)
            if ln in want and ln not in out:
                out[ln] = (e.text or "").strip()
        return out

    # ── 目录 ────────────────────────────────────────────────────
    def _toc(self, z: zipfile.ZipFile, opf_dir: str, manifest: dict[str, dict[str, str]]):
        """返回 (entries, source)。entries 为 [(level, title, href, zip_inner_path|None, fragment)]。"""
        for it in manifest.values():
            if "nav" in it["props"]:
                nav_path = _norm(opf_dir, it["href"])
                nav_dir = posixpath.dirname(nav_path)
                doc = _read_xml(z, nav_path)
                navs = doc.xpath("//*[local-name()='nav' and @*[local-name()='type']='toc']") or \
                       doc.xpath("//*[local-name()='nav']")
                entries = []
                if navs:
                    for li in navs[0].xpath(".//*[local-name()='li']"):
                        a = li.xpath("./*[local-name()='a']")
                        if not a:
                            continue
                        href = a[0].get("href") or ""
                        level = len(li.xpath("ancestor::*[local-name()='ol']"))
                        title = _itertext(a[0])
                        file_part, _, frag = href.partition("#")
                        inner = _norm(nav_dir, file_part) if file_part else None
                        entries.append((level, title, href, inner, frag or None))
                return entries, "nav"
        for it in manifest.values():
            if "dtbncx" in it["type"]:
                ncx_path = _norm(opf_dir, it["href"])
                root = _read_xml(z, ncx_path)
                ns = "{http://www.daisy.org/z3986/2005/ncx/}"
                entries = []
                for p in root.iter(f"{ns}navPoint"):
                    level = len(list(p.iter(f"{ns}navPoint")))
                    label = p.find(f".//{ns}text")
                    content = p.find(f"{ns}content")
                    src = content.get("src") if content is not None else ""
                    file_part, _, frag = src.partition("#")
                    entries.append((level, (label.text or "").strip() if label is not None else "",
                                    src, _norm(opf_dir, file_part) if file_part else None,
                                    frag or None))
                return entries, "ncx"
        return [], "none"

    # ── 主流程 ──────────────────────────────────────────────────
    def extract(self, assets_dir: Path | None = None) -> DocumentIR:
        z = self._open()
        try:
            opf_path, opf = self._opf(z)
            opf_dir = posixpath.dirname(opf_path)
            manifest = self._manifest(opf)
            spine = self._spine(opf)
            meta = self._meta(opf)
            toc_raw, toc_src = self._toc(z, opf_dir, manifest)

            # 每个章节文件 → 目录条目（取第一条指向该文件的）
            by_file: dict[str, tuple[int, str, str | None]] = {}
            for level, title, _href, inner, frag in toc_raw:
                if inner and inner not in by_file:
                    by_file[inner] = (level, title, frag)

            # 竖排检测：扫描 CSS
            vertical = False
            for it in manifest.values():
                if "css" not in it["type"]:
                    continue
                try:
                    css = z.read(_norm(opf_dir, it["href"])).decode("utf-8", "ignore")
                except KeyError:
                    continue
                if _VERTICAL.search(css):
                    vertical = True
                    break

            blocks: list[Block] = []
            toc: list[TocEntry] = []
            docs = [manifest[s] for s in spine if s in manifest and manifest[s]["type"] in XHTML_TYPES]
            for d in docs:
                inner = _norm(opf_dir, d["href"])
                try:
                    raw = z.read(inner)
                except KeyError:
                    continue
                root = etree.fromstring(raw, etree.XMLParser(recover=True, no_network=True))
                info = by_file.get(inner)
                title = info[1] if info else ""
                level = info[0] if info else 1
                frag = info[2] if info else None
                head_id, head_text = self._emit_doc(root, inner, blocks, title, level, frag)
                if head_id:
                    toc.append(TocEntry(level=level, title=title or head_text,
                                        href=d["href"], block_id=head_id))

            # 图片（按需提取）
            if assets_dir is not None:
                self._extract_images(z, opf_dir, manifest, assets_dir)

            return DocumentIR(
                doc=DocMeta(
                    id=self.doc_id or slugify(meta.get("title") or self.path.stem),
                    title=meta.get("title", ""),
                    author=meta.get("creator", ""),
                    publisher=meta.get("publisher", ""),
                    source_lang=meta.get("language", ""),
                    origin="epub",
                    vertical=vertical,
                    ruby_dropped=self._ruby_dropped,
                    spine_docs=len(docs),
                ),
                toc=toc,
                blocks=blocks,
            )
        finally:
            z.close()

    def _next_id(self) -> str:
        self._block_seq += 1
        return f"b{self._block_seq:06d}"

    def _emit_doc(self, root: Any, inner: str, blocks: list[Block], title: str,
                  level: int, frag: str | None) -> tuple[str | None, str]:
        """把一个章节 XHTML 转成 blocks，返回 (标题块 id, 标题文本)。

        标题文本用于回填 TOC：NAV 标题为空时（真实样书的「あとがき」页就是如此），
        由正文兜底识别出的标题补上。
        """
        # XHTML 带命名空间（{http://www.w3.org/1999/xhtml}body），不能只用 find("body")
        body = next((e for e in root.iter() if localname(e.tag) == "body"), None)
        if body is None:
            return None, ""
        # 前后附页归类：整个 XHTML 文件同属一类（封面/口絵/目次/奥付/广告…），
        # 先记下起始下标，正文处理完统一盖章，避免每处 Block 构造都传一遍。
        matter = classify_matter(inner, title)
        matter_from = len(blocks)
        head_id: str | None = None
        head_text = ""
        head_emitted = False
        # 已并入脚注/表格子树的元素，避免重复产出。
        # **必须持有强引用**：lxml 的元素是代理对象，一旦被回收，`id()` 会被后续
        # 新代理复用，导致无关元素被误判为"已处理"而整块丢掉（实测脚注就是这么消失的）。
        claimed: list[Any] = []
        claimed_ids: set[int] = set()

        # 标题块：优先用 NAV 锚点定位；找不到锚点则用 NAV 标题直接生成
        if title and not frag:
            head_id = self._next_id()
            blocks.append(Block(id=head_id, type="heading", level=level, text=title,
                                src="nav", source_ref=inner))
            head_text = title
            head_emitted = True

        for el in body.iter():
            if not isinstance(el.tag, str):
                continue
            if id(el) in claimed_ids:  # 已被脚注/表格整棵收纳，不再单独产出块
                continue
            tag = localname(el.tag)
            if tag in ("html", "body", "head", "script", "style", "title"):
                continue
            # ① 主路径：NAV 锚点命中（M0 实测 16/16 命中）
            if frag and el.get("id") == frag:
                self._ruby_dropped += count_rt(el)
                head_text = title or text_without_rt(el)
                head_id = self._next_id()
                blocks.append(Block(id=head_id, type="heading", level=level, text=head_text,
                                    src="nav", source_ref=inner, spans=_spans_of(el)))
                head_emitted = True
                continue
            # ② 脚注（M2）：整块收成一个 footnote 块，子树不再重复产出
            if _is_footnote(el):
                text = text_without_rt(el)
                self._ruby_dropped += count_rt(el)
                if text:
                    blocks.append(Block(id=self._next_id(), type="footnote", text=text,
                                        note_id=el.get("id") or "", src="note",
                                        source_ref=inner, spans=_spans_of(el)))
                claimed.extend(el.iter())
                claimed_ids.update(id(d) for d in claimed)
                continue
            # ③ 表格（M2）：保留行列结构（含合并信息），否则整张表会被丢弃
            if tag == "table":
                rows, spans = self._table_rows(el)
                if any(c.strip() for r in rows for c in r):
                    cap = next((text_without_rt(c) for c in el.iter()
                                if localname(c.tag) == "caption"), "")
                    blocks.append(Block(id=self._next_id(), type="table", rows=rows,
                                        cell_spans=spans, caption=cap, src="body",
                                        source_ref=inner))
                claimed.extend(el.iter())
                claimed_ids.update(id(d) for d in claimed)
                continue
            if tag in ("img", "image"):
                # `<img src>` 覆盖正文插图；**整页图走的是 `<svg><image xlink:href>`**
                # （封面/裏表紙/口絵/扉絵/广告页）。只认 `<img>` 会让这 10/22 张图
                # 只被打包、从不显示——实测真实样书踩过。
                src = (el.get("src") or el.get("href")
                       or el.get(f"{{{XLINK_NS}}}href") or "")
                if src and not src.startswith("data:"):
                    blocks.append(Block(id=self._next_id(), type="image",
                                        path=src.replace("\\", "/"), source_ref=inner))
                continue
            if tag == "p" or (tag == "div" and text_without_rt(el) and not list(el)):
                text = text_without_rt(el)
                self._ruby_dropped += count_rt(el)
                if is_blank_block(text):
                    continue
                # ② 兜底：本文件尚无标题块，且类名像标题 → 视为标题
                #    （真实样书的 NAV 对「あとがき」页标题为空，走的就是这条）
                if not head_emitted and _HEAD_CLASS.search(el.get("class") or "") and len(text) <= 40:
                    head_id = self._next_id()
                    blocks.append(Block(id=head_id, type="heading", level=level, text=text,
                                        src="nav-heuristic", source_ref=inner,
                                        spans=_spans_of(el)))
                    head_text = text
                    head_emitted = True
                    continue
                blocks.append(Block(id=self._next_id(), type="paragraph", text=text,
                                    src="body", source_ref=inner, spans=_spans_of(el),
                                    refs=_noterefs(el)))
        for b in blocks[matter_from:]:
            b.matter = matter
        return head_id, head_text

    @staticmethod
    def _table_rows(el: Any) -> tuple[list[list[str]], list[list[list[int]]]]:
        """把 `<table>` 抽成行列文本 + 合并信息。

        **不能只取纯文本**：表格一旦被压成一串字，行列对应关系就丢了，
        译文也就无法还原成表格。合并单元格的 `rowspan/colspan` 一并记下。
        """
        rows: list[list[str]] = []
        spans: list[list[list[int]]] = []
        for tr in el.iter():
            if localname(tr.tag) != "tr":
                continue
            cells: list[str] = []
            sp: list[list[int]] = []
            for td in tr:
                if not isinstance(td.tag, str) or localname(td.tag) not in ("td", "th"):
                    continue
                cells.append(text_without_rt(td))

                # 用默认参数把当轮的 `td` **绑进闭包**：否则 `_n` 引用的是循环变量，
                # 等它被调用时指向的已经是别的单元格（B023 说的就是这件事）。
                def _n(attr: str, _td=td) -> int:
                    try:
                        return max(1, int(_td.get(attr) or 1))
                    except (TypeError, ValueError):
                        return 1

                sp.append([_n("rowspan"), _n("colspan")])
            if cells:
                rows.append(cells)
                spans.append(sp)
        return rows, spans

    def _extract_images(self, z: zipfile.ZipFile, opf_dir: str,
                        manifest: dict[str, dict[str, str]], assets_dir: Path) -> None:
        assets_dir.mkdir(parents=True, exist_ok=True)
        for it in manifest.values():
            href = it["href"]
            if not href.lower().endswith(_IMG_EXT):
                continue
            inner = _norm(opf_dir, href)
            try:
                data = z.read(inner)
            except KeyError:
                continue
            name = posixpath.basename(inner)
            target = assets_dir / name
            if not target.exists():
                target.write_bytes(data)
