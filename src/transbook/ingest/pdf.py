"""PDF → DocumentIR 抽取器（pypdfium2）。

引擎选型来自 M0 实测：pypdfium2 与 PyMuPDF 抽取结果**完全一致**但快 3 倍，许可宽松，
且能直读书签（本书 12 条，与 EPUB 的 NAV 一一对应）。

**竖排（縦書き）处理**——本模块的核心难点，实测结论如下：
* 竖排 PDF 的文字层按 **列** 排列：列首在右，逐列向左；列内自上而下。
* pypdfium2 的 `get_text_range()` **已经给出正确的阅读顺序**（列序与列内顺序都对）——
  实测用同一本书的 EPUB 孪生文本逐段比对：**200/200 完全命中**。因此**不需要列序重排**。
* `\\r\\n` 是**列边界**，不是段落边界。
* 段落靠 **列首字下げ**（首字下沉约一个字位）识别：实测 dy≈0 是续行、dy≈1 字是**新段落**、
  dy≫1 字是**振假名**或列中片段。
* 竖排 PDF 的假名注音**内联在文字流里**（`尽くし難がたい`），无标记可剥——实测对翻译无碍
  （与 EPUB 的 `<rt>` 等价），故保留并在 IR 里计数。
"""

from __future__ import annotations

import posixpath
import re
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from transbook.ir import Block, DocumentIR, DocMeta, TocEntry
from transbook.textutil import (classify_matter, clean_paragraph,
                                clean_pdf_text, join_wrapped, normalize_ws)

_WS = re.compile(r"[\s\u3000]+")
_KANA = re.compile(r"[\u3041-\u309f\u30a1-\u30f6]")
#: 短假名串（1~4 字）≈ 振假名候选（日文正文里也会有假名，故只作粗略计数）
_KANA_RUN = re.compile(r"[\u3041-\u309f\u30a1-\u30f6]{1,4}")

ASCENT_MAX = 12_000


class PdfError(RuntimeError):
    """PDF 结构或解析异常。"""


# ── 纯函数：几何判定（可单测，不依赖真实 PDF）─────────────────────────
def detect_vertical(samples: Iterable[tuple[float, float]]) -> bool:
    """按字符推进方向判定竖排。

    `samples` 为相邻字符的位移序列 `(dx, dy)`：
    * 竖排：字符沿 y 推进（|dy| 明显大于 |dx|）
    * 横排：字符沿 x 推进

    注意：**不要**用 PyMuPDF 的行方向向量判断——竖排文档里它会把"同一水平高度的各列切片"
    当成一行，从而误报为横排（M0 踩过这个坑）。
    """
    pairs = [(abs(dx), abs(dy)) for dx, dy in samples if abs(dx) + abs(dy) > 0.01]
    if not pairs:
        return False
    dx = statistics.median(p[0] for p in pairs)
    dy = statistics.median(p[1] for p in pairs)
    return dy > dx * 1.5


def char_height(boxes: Iterable[tuple[float, float, float, float]]) -> float:
    """字符高度中位数（竖排/横排都用高度，因为竖排的"字位"是纵向的）。"""
    hs = sorted(b[3] - b[1] for b in boxes if (b[3] - b[1]) > 0.5)
    return hs[len(hs) // 2] if hs else 12.0


def segment_kind(dy_from_top: float, h: float, *, lo: float = 0.30,
                 hi: float = 1.80) -> str:
    """判断一个列段相对文本区顶部的位置类型（`h` 为字符高度）。

    * `cont` ：贴近顶部（< 0.3 字）→ 上一列的**续行**
    * `start`：下沉约一个字位（0.3~1.8 字）→ **新段落**（字下げ）
    * `inner`：明显低于顶部（≥ 1.8 字）→ **振假名**或列中片段

    后两者在还原段落时都并入当前段落，区分开是为了可读性与可测性。
    """
    if dy_from_top < lo * h:
        return "cont"
    if dy_from_top < hi * h:
        return "start"
    return "inner"


def segments_to_paragraphs(segments: list[tuple[str, float]], h: float, *,
                           vertical: bool = True) -> list[str]:
    """把「列段 + 首字相对顶部偏移」序列还原成段落列表。

    `vertical` 决定换行拼接规则：竖排列边界直接相连；横排要区分**跨行断词**
    （`trans-` + `lation`）与**正常换行**（补空格）。见 `textutil.join_wrapped`。
    默认 `True` 保持竖排既有行为。
    """
    paras: list[str] = []
    cur = ""
    for text, dy in segments:
        t = clean_pdf_text(text)
        if not t:
            continue
        if segment_kind(dy, h) == "start" and cur:
            paras.append(cur)
            cur = t
        else:
            cur = join_wrapped(cur, t, vertical=vertical)
    if cur:
        paras.append(cur)
    return [clean_paragraph(p) for p in paras]


# ── 抽取器 ─────────────────────────────────────────────────────────
@dataclass
class PdfStats:
    pages: int = 0
    chars: int = 0
    paragraphs: int = 0
    headings: int = 0
    images: int = 0
    vertical: bool = False
    ruby_runs: int = 0
    bookmarks: int = 0


class PdfIngestor:
    """把 PDF 解析成 DocumentIR。结构与 EPUB 路径共用同一套 Block/TocEntry。"""

    def __init__(self, path: str | Path, doc_id: str | None = None,
                 min_chars_per_para: int = 1) -> None:
        self.path = Path(path)
        self.doc_id = doc_id
        self.min_chars = min_chars_per_para
        self._seq = 0
        self.stats = PdfStats()

    # ── 工具 ────────────────────────────────────────────────────
    def _next_id(self) -> str:
        self._seq += 1
        return f"b{self._seq:06d}"

    def _open(self):
        try:
            import pypdfium2 as pdfium
        except ImportError as exc:  # pragma: no cover
            raise PdfError("未安装 pypdfium2（uv add pypdfium2）") from exc
        if not self.path.is_file():
            raise PdfError(f"文件不存在: {self.path}")
        return pdfium.PdfDocument(self.path)

    # ── 页面级解析 ──────────────────────────────────────────────
    def _page_chars(self, page) -> tuple[str, list[tuple[float, float, float, float]], float,
                                         list[tuple[float, float]]]:
        """取本页原始文本、字符框、字符高度、相邻字符位移样本。

        这一层较贵（逐字符 `get_charbox`），每页只算一次。切段规则是可变的
        （横排/竖排的版心参考边不同），所以拆到 `_page_segments` 里做。
        """
        tp = page.get_textpage()
        raw = tp.get_text_range()
        n = tp.count_chars()
        boxes: list[tuple[float, float, float, float]] = []
        for i in range(n):
            try:
                boxes.append(tp.get_charbox(i))
            except Exception:  # noqa: BLE001
                boxes.append((0.0, 0.0, 0.0, 0.0))
        h = char_height(boxes)

        # 相邻字符位移（用于竖排判定）
        samples = []
        for i in range(1, min(n, 400)):
            samples.append((boxes[i][0] - boxes[i - 1][0], boxes[i][1] - boxes[i - 1][1]))
        return raw, boxes, h, samples

    def _page_segments(self, raw: str, boxes: list[tuple[float, float, float, float]],
                       h: float, *, vertical: bool) -> list[tuple[str, float]]:
        """切出「列段/行段」，并给出首字相对**版心参考边**的偏移量。

        参考边的选择就是段落判定的核心，两个方向完全不同：

        * **竖排**：参考边是本页**最高的字符底边**（`max(y3)`），偏移量 = 首字下移量；
          `字下げ`（≈1 字）即新段落。**参考边必须每页独立取**——各页版心位置不同，
          沿用全书统一值会让段落数从 3388 掉到 340（实测踩过）。
        * **横排**：参考边是本页**最左的字符左沿**（`min(x0)`），偏移量 = 首行缩进量；
          缩进 ≈1 字即新段落，齐头行则是上一行的续接。另有一条兜底：若行间空隙
          明显大于常规行距（**空行分段**，LaTeX / 网页导出的 PDF 常见），
          把该行按 1 字缩进处理。
        """
        if vertical:
            refs = [b[3] for b, c in zip(boxes, raw) if c.strip()]
            ref = max(refs) if refs else 0.0
            off_of = lambda b: ref - b[3]  # noqa: E731
        else:
            refs = [b[0] for b, c in zip(boxes, raw) if c.strip()]
            ref = min(refs) if refs else 0.0
            off_of = lambda b: b[0] - ref  # noqa: E731

        # 先收集 (段文本, 首字符框)，横排还要用相邻行的位置算行距
        raw_segs: list[tuple[str, tuple[float, float, float, float] | None]] = []
        pos = 0
        for part in raw.split("\r\n"):
            if part.strip():
                first = pos
                for j, ch in enumerate(part):
                    if ch.strip():
                        first = pos + j
                        break
                raw_segs.append((part, boxes[first] if first < len(boxes) else None))
            pos += len(part) + 2

        # 行间空白 = 上一行底边 - 本行顶边（PDF 坐标 y 向上，故上一行 y 更大）
        gaps: list[float] = []
        if not vertical:
            for (_, pa), (_, pb) in zip(raw_segs, raw_segs[1:]):
                if pa and pb:
                    gaps.append(pa[1] - pb[3])
        med_gap = statistics.median(gaps) if gaps else 0.0

        segments: list[tuple[str, float]] = []
        for i, (part, box) in enumerate(raw_segs):
            off = off_of(box) if box else 0.0
            if not vertical and i > 0 and med_gap > 0 and i - 1 < len(gaps):
                if gaps[i - 1] > med_gap * 1.6 + h * 0.4:
                    off = max(off, h)  # 空行 → 按字下げ处理，即新段落
            segments.append((part, off))
        return segments

    # ── 图片提取 ────────────────────────────────────────────────
    def _page_images(self, page, assets_dir: Path, pi: int) -> list[str]:
        """抽出本页的内嵌图片，返回保存后的文件名列表。

        过滤小于 64px 的图（页码装饰、线条等）；同名去重；失败不中断（插图缺失不应毁掉整本书）。
        """
        try:
            import pypdfium2 as pdfium
        except ImportError:  # pragma: no cover
            return []
        saved: list[str] = []
        try:
            objs = list(page.get_objects())
        except Exception:  # noqa: BLE001
            return []
        for k, obj in enumerate(objs, start=1):
            if not isinstance(obj, pdfium.PdfImage):
                continue
            try:
                w, h = obj.get_px_size()
            except Exception:  # noqa: BLE001
                continue
            if min(w, h) < 64:
                continue
            # 同一个 base 名可能残留上一轮抽取的其它扩展名，先清掉，避免重复打包
            base = assets_dir / f"p{pi + 1:04d}_{k}"
            for old in assets_dir.glob(f"{base.name}.*"):
                try:
                    old.unlink()
                except OSError:  # pragma: no cover
                    pass
            # ① 优先 `extract()`：DCTDecode(JPEG) 等能**原样抽出内嵌流**，无重编码损失，
            #    体积也小三倍多（实测 3340 KB/张 → 943 KB/张；整本 49.7 MB → ~15 MB）。
            got: Path | None = None
            try:
                obj.extract(dest=base)  # 写入 `{base}.{真实扩展名}`
                cands = sorted(assets_dir.glob(f"{base.name}.*"))
                got = cands[0] if cands else None
            except Exception:  # noqa: BLE001 - 个别编码（CMYK/JPX/带遮罩）会失败
                got = None
            # ② 退路：解成位图再存 PNG（体积大但一定能出图）
            if got is None:
                png = base.with_suffix(".png")
                try:
                    img = obj.get_bitmap().to_pil()
                    if img.mode not in ("RGB", "RGBA"):
                        img = img.convert("RGB")
                    img.save(png)
                    got = png
                except Exception:  # noqa: BLE001
                    continue
            saved.append(got.name)
        return saved

    # ── 主流程 ──────────────────────────────────────────────────
    def extract(self, assets_dir: Path | None = None) -> DocumentIR:
        doc = self._open()
        if assets_dir is not None:
            Path(assets_dir).mkdir(parents=True, exist_ok=True)
        try:
            pages = len(doc)
            self.stats.pages = pages
            meta_title, meta_author = self._meta(doc)
            toc = self._bookmarks(doc)  # [(level, title, page_index)]
            self.stats.bookmarks = len(toc)
            # 一页可能有多个书签（同一页的段落级条目），用列表而不是字典，避免互相覆盖
            by_page: dict[int, list[tuple[int, str]]] = {}
            for lvl, title, pi in toc:
                by_page.setdefault(pi, []).append((lvl, title))

            blocks: list[Block] = []
            toc_entries: list[TocEntry] = []

            # 预判竖排：换行拼接规则依赖排版方向（竖排列边界直接相连；横排要处理跨行断词
            # 与补空格），**必须先于正文定下来**。前置页常是纯图（表紙），故向后探测到
            # 攒够 200 个位移样本为止，最多 20 页；这几页结果缓存，主循环不重复算。
            probe: list[tuple[float, float]] = []
            cache: dict[int, tuple[str, list[tuple[float, float, float, float]], float]] = {}
            for pi in range(min(20, pages)):
                raw, boxes, h, samples = self._page_chars(doc[pi])
                cache[pi] = (raw, boxes, h)
                probe.extend(samples)
                if len(probe) >= 200:
                    break
            vertical = detect_vertical(probe)
            cur_matter = "main"

            for pi in range(pages):
                page = doc[pi]
                if pi in cache:
                    raw, boxes, h = cache[pi]
                else:
                    raw, boxes, h, _samples = self._page_chars(page)
                segments = self._page_segments(raw, boxes, h, vertical=vertical)

                # 本页归类：书签标题优先；书首的**纯图页**（表紙/口絵）没有书签可依，
                # 按位置判——这是印本 PDF 的稳定版式约定。
                page_matter = cur_matter
                for _lvl, _t in by_page.get(pi, []):
                    page_matter = classify_matter("", _t)
                if not segments and pi < 3:
                    page_matter = "cover" if pi == 0 else "front"

                # 图片要在"无文字的页"判断之前处理：**纯插图页没有文字**，
                # 若先 continue 会整页漏掉插画（实测：只抽到最后一页的小图，应为 28 张）。
                if assets_dir is not None:
                    for name in self._page_images(page, Path(assets_dir), pi):
                        self.stats.images += 1
                        blocks.append(Block(id=self._next_id(), type="image", path=name,
                                            source_ref=f"p{pi + 1}", matter=page_matter))
                if not segments:
                    continue

                # 章节标题（来自书签）在本页开始时先落标题块
                for lvl, title in by_page.get(pi, []):
                    bid = self._next_id()
                    cur_matter = classify_matter("", title)
                    blocks.append(Block(id=bid, type="heading", level=lvl, text=title,
                                        src="bookmark", source_ref=f"p{pi + 1}",
                                        matter=cur_matter))
                    toc_entries.append(TocEntry(level=lvl, title=title, href=f"p{pi + 1}",
                                                block_id=bid))
                    self.stats.headings += 1

                page_titles = [t for _, t in by_page.get(pi, [])]
                paras = segments_to_paragraphs(segments, h, vertical=vertical)
                for p in paras:
                    if len(p) < self.min_chars:
                        continue
                    # 竖排的首段常与章节标题同列 → 去掉重复的标题前缀
                    for title in page_titles:
                        p = _strip_title_prefix(p, title)
                    if not p.strip():
                        continue
                    self.stats.ruby_runs += len(_KANA_RUN.findall(p))
                    self.stats.paragraphs += 1
                    blocks.append(Block(id=self._next_id(), type="paragraph", text=p,
                                        src="body", source_ref=f"p{pi + 1}",
                                        matter=cur_matter))
                self.stats.chars += sum(len(t) for t, _ in segments)

            self.stats.vertical = vertical
            return DocumentIR(
                doc=DocMeta(id=self.doc_id or _slug(meta_title or self.path.stem),
                            title=meta_title, author=meta_author, source_lang="ja",
                            origin="pdf", vertical=self.stats.vertical,
                            page_count=pages),
                toc=toc_entries, blocks=blocks)
        finally:
            doc.close()

    # ── 元数据与书签 ────────────────────────────────────────────
    @staticmethod
    def _meta(doc) -> tuple[str, str]:
        try:
            m = doc.get_metadata_dict()
        except Exception:  # noqa: BLE001
            m = {}
        return (m.get("Title") or "", m.get("Author") or "")

    @staticmethod
    def _bookmarks(doc) -> list[tuple[int, str, int]]:
        out: list[tuple[int, str, int]] = []
        try:
            for bm in doc.get_toc():
                title = bm.get_title() if hasattr(bm, "get_title") else ""
                dest = bm.get_dest() if hasattr(bm, "get_dest") else None
                pi = None
                for getter in ("get_index", "get_page_index"):
                    if hasattr(dest, getter):
                        pi = getattr(dest, getter)()
                        break
                if pi is None:
                    for attr in ("index", "page_index"):
                        if hasattr(dest, attr):
                            pi = getattr(dest, attr)
                            break
                if isinstance(pi, int) and title:
                    out.append((int(getattr(bm, "level", 0)) + 1, title.strip(), pi))
        except Exception:  # noqa: BLE001 - 无书签不影响正文抽取
            pass
        return out


def _strip_title_prefix(para: str, title: str) -> str:
    """首段常与章节标题粘在一起（`プロローグ『夢の終わり』 ── 夢の城に…`），去掉标题部分。"""
    t = normalize_ws(title)
    if not t:
        return para
    if para.startswith(t):
        return para[len(t):].lstrip(" 　─-—")
    return para


def _slug(text: str) -> str:
    from transbook.ingest.epub import slugify

    return slugify(text)
