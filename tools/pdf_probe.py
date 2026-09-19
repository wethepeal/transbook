#!/usr/bin/env python3
"""PDF 抽取对比（M0 实测 #6）。

对比 pypdfium2（Apache/BSD）、pdfminer.six（MIT）、PyMuPDF（AGPL，仅作对照）在
**真实文字版日文 PDF** 上的表现，重点回答三个问题：

  ① 文字层能不能正常取出（字符数、耗时）
  ② **竖排**文本的阅读顺序是否可还原（靠行方向 dir 判断）
  ③ 三个引擎的结果是否一致（字符数差异、样本文本对照）

用法：uv run --with pypdfium2 --with pdfminer.six --with pymupdf python tools/pdf_probe.py <book.pdf>
"""

from __future__ import annotations

import re
import sys
import time
from collections import Counter

PDF = sys.argv[1] if len(sys.argv) > 1 else ""
SAMPLE_PAGE = 12  # 通常前几页是封面/目录，取中间一页看正文


def clean(s: str) -> str:
    return re.sub(r"[\s\u3000]+", "", s or "")


# ── 引擎 1：PyMuPDF（对照，提供行方向等结构信息）──────────────────────────
def via_pymupdf(path: str) -> dict:
    import fitz

    t0 = time.perf_counter()
    doc = fitz.open(path)
    chars = 0
    dirs: Counter = Counter()
    fonts: Counter = Counter()
    page_chars = []
    for page in doc:
        d = page.get_text("dict")
        n = 0
        for block in d.get("blocks", []):
            for line in block.get("lines", []):
                dirs[tuple(round(v) for v in line.get("dir", (1, 0)))] += 1
                for span in line.get("spans", []):
                    n += len(clean(span.get("text", "")))
                    fonts[span.get("font", "?")] += 1
        page_chars.append(n)
        chars += n
    toc = doc.get_toc()
    imgs = sum(len(p.get_images(full=True)) for p in doc)
    sample = ""
    if len(doc) > SAMPLE_PAGE:
        sample = clean(doc[SAMPLE_PAGE].get_text())
    info = {
        "pages": len(doc),
        "chars": chars,
        "sec": time.perf_counter() - t0,
        "dirs": dirs,
        "fonts": fonts,
        "page_chars": page_chars,
        "toc": toc,
        "images": imgs,
        "sample": sample[:120],
        "meta": dict(doc.metadata or {}),
        "encrypted": doc.is_encrypted,
    }
    doc.close()
    return info


# ── 引擎 2：pypdfium2（宽松许可，PDFium）────────────────────────────────
def via_pypdfium2(path: str) -> dict:
    import pypdfium2 as pdfium

    t0 = time.perf_counter()
    doc = pdfium.PdfDocument(path)
    texts = []
    for page in doc:
        texts.append(page.get_textpage().get_text_range())
    sec = time.perf_counter() - t0
    joined = "".join(texts)
    return {
        "pages": len(doc),
        "chars": len(clean(joined)),
        "sec": sec,
        "sample": clean(texts[SAMPLE_PAGE] if len(texts) > SAMPLE_PAGE else "")[:120],
        "page_chars": [len(clean(t)) for t in texts],
    }


# ── 引擎 3：pdfminer.six（MIT）──────────────────────────────────────────
def via_pdfminer(path: str) -> dict:
    from pdfminer.high_level import extract_text

    t0 = time.perf_counter()
    text = extract_text(path, page_numbers=list(range(min(30, 10_000))))
    sec = time.perf_counter() - t0
    pages = text.split("\f")
    return {
        "pages_scanned": len(pages) - 1,
        "chars": len(clean(text)),
        "sec": sec,
        "sample": clean(pages[SAMPLE_PAGE] if len(pages) > SAMPLE_PAGE else "")[:120],
    }


def main() -> None:
    print(f"PDF: {PDF}\n")
    results: dict[str, dict] = {}
    for name, fn in (("pymupdf", via_pymupdf), ("pypdfium2", via_pypdfium2), ("pdfminer.six", via_pdfminer)):
        try:
            results[name] = fn(PDF)
        except Exception as exc:  # noqa: BLE001
            results[name] = {"error": f"{type(exc).__name__}: {exc}"[:160]}

    mu = results.get("pymupdf", {})
    if "error" not in mu:
        print("【结构信息（PyMuPDF 提供）】")
        print(f"  页数: {mu['pages']} ｜ 文字字符数: {mu['chars']:,} ｜ 图片对象: {mu['images']}")
        print(f"  加密: {mu['encrypted']} ｜ 书签目录: {len(mu['toc'])} 条")
        if mu["toc"]:
            for lvl, title, page in mu["toc"][:6]:
                print(f"     L{lvl} {title[:36]:<38} p.{page}")
        print(f"  元数据: { {k: v for k, v in list(mu['meta'].items())[:5]} }")
        print("\n  【行方向分布】← 判断横排/竖排的关键")
        for d, n in mu["dirs"].most_common(6):
            kind = "横排" if d == (1, 0) else ("竖排" if d == (0, 1) or d == (0, -1) else f"其它{d}")
            print(f"     dir={d}  行数={n:>6}   {kind}")
        print("\n  【字体使用 Top6】")
        for f, n in mu["fonts"].most_common(6):
            print(f"     {f:<44} {n:>6}")
        pc = mu["page_chars"]
        if pc:
            empty = sum(1 for c in pc if c < 5)
            print(f"\n  【文字层分布】前 10 页字符数: {pc[:10]}")
            print(f"     近似空页（<5 字）: {empty}/{len(pc)}  → "
                  f"{'疑似扫描件' if empty > len(pc) * 0.5 else '文字版 PDF ✅'}")
    else:
        print("PyMuPDF 失败:", mu["error"])

    print("\n【抽取引擎对比】")
    print(f"{'引擎':<14}{'字符数':>10}{'耗时(秒)':>10}{'相对基准':>10}  样本（首页正文）")
    base = mu.get("chars") or 0
    for name, r in results.items():
        if "error" in r:
            print(f"{name:<14}{'FAIL':>10}{'-':>10}{'-':>10}  {r['error']}")
            continue
        ratio = f"{r['chars'] / base * 100:.0f}%" if base else "—"
        print(f"{name:<14}{r['chars']:>10,}{r['sec']:>10.2f}{ratio:>10}  {r.get('sample','')[:40]!r}")

    print("\n【竖排结论】")
    if "error" not in mu:
        vert = sum(n for d, n in mu["dirs"].items() if d in ((0, 1), (0, -1)))
        total = sum(mu["dirs"].values()) or 1
        share = vert / total * 100
        if share > 20:
            print(f"  ⚠️ 竖排行占比 {share:.0f}% → 这是**竖排 PDF**，抽取时必须做列序重排（右→左、上→下）")
        elif share > 0:
            print(f"  竖排行占比 {share:.0f}%（少量，可能是竖排标题或数字）")
        else:
            print("  未检出竖排行 → 横排 PDF")

    # ── 正文质量抽样：找第一页有字的页，逐引擎对照 ─────────────────────
    print("\n【正文抽样（第一个有文字的页面）】")
    if "error" not in mu:
        pc = mu["page_chars"]
        idx = next((i for i, c in enumerate(pc) if c > 150), None)
        print(f"  抽样页: 第 {idx + 1 if idx is not None else '—'} 页（前 10 页字符数 {pc[:10]}）")
    import pypdfium2 as _pdfium  # noqa: PLC0415

    doc2 = _pdfium.PdfDocument(PDF)
    first = next((i for i in range(len(doc2)) if len(clean(doc2[i].get_textpage().get_text_range())) > 150), 0)
    print(f"  ---- pypdfium2 抽取（第 {first + 1} 页，前 200 字）----")
    print("  " + clean(doc2[first].get_textpage().get_text_range())[:200])

    # pypdfium2 能否直接读书签（决定是否还需要 PyMuPDF）
    try:
        toc = list(doc2.get_toc())
        print(f"\n  pypdfium2 书签支持: ✅ 读到 {len(toc)} 条")
        if toc:
            def _page_index(bm) -> object:
                dest = bm.get_dest() if hasattr(bm, "get_dest") else None
                if dest is None:
                    return "?"
                for getter in ("get_index", "get_page_index"):
                    if hasattr(dest, getter):
                        return getattr(dest, getter)()
                for attr in ("index", "page_index"):
                    if hasattr(dest, attr):
                        return getattr(dest, attr)
                return "?"

            for bm in toc[:8]:
                title = bm.get_title() if hasattr(bm, "get_title") else "?"
                pi = _page_index(bm)
                page_no = pi + 1 if isinstance(pi, int) else pi
                print(f"     L{getattr(bm, 'level', '?')} {str(title)[:34]:<36} -> 第 {page_no} 页")
    except Exception as exc:  # noqa: BLE001
        print(f"\n  pypdfium2 书签支持: ❌ {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    main()
