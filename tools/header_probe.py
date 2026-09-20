"""探测 PDF 的页眉/页脚候选：跨页重复 + 位置一致 + 贴上下边。

用法：python tools/header_probe.py <pdf>
只读统计，不改任何文件。
"""

from __future__ import annotations

import collections
import re
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pypdfium2 as pdfium  # noqa: E402

from transbook.ingest.pdf import char_height, detect_vertical  # noqa: E402
from transbook.textutil import normalize_ws  # noqa: E402

NUM = re.compile(r"[0-9０-９]{1,4}|[ivxlcIVXLC]{1,6}")


def main() -> None:
    path = Path(sys.argv[1])
    doc = pdfium.PdfDocument(path)
    pages = len(doc)
    print(f"{path.name}  页数 {pages}")

    per_page = []
    for pi in range(pages):
        tp = doc[pi].get_textpage()
        raw = tp.get_text_range()
        n = tp.count_chars()
        boxes = []
        for i in range(n):
            try:
                boxes.append(tp.get_charbox(i))
            except Exception:  # noqa: BLE001
                boxes.append((0.0, 0.0, 0.0, 0.0))
        segs = []
        pos = 0
        for part in raw.split("\r\n"):
            if part.strip():
                first = pos
                for j, ch in enumerate(part):
                    if ch.strip():
                        first = pos + j
                        break
                segs.append((normalize_ws(part), boxes[first] if first < len(boxes) else None))
            pos += len(part) + 2
        per_page.append((char_height(boxes), segs, boxes))

    samples = []
    for h, segs, boxes in per_page[:20]:
        for i in range(1, min(len(boxes), 400)):
            samples.append((boxes[i][0] - boxes[i - 1][0], boxes[i][1] - boxes[i - 1][1]))
    vertical = detect_vertical(samples)
    print("竖排:", vertical)

    # text -> {page: [归一化 y, 归一化 x]}
    stat: dict[str, dict[int, tuple[float, float]]] = collections.defaultdict(dict)
    for pi, (h, segs, boxes) in enumerate(per_page):
        ys = [b[1] for b in boxes if b[3] > b[1]]
        xs = [b[0] for b in boxes if b[2] > b[0]]
        if not ys or not xs:
            continue
        y0, y1 = min(ys), max(ys)
        x0, x1 = min(xs), max(xs)
        ysl, xsl = (y1 - y0) or 1.0, (x1 - x0) or 1.0
        for text, b in segs:
            if not b or not text or len(text) > 40:
                continue
            # y 归一化：0 = 页面最下方，1 = 页面最上方
            stat[text].setdefault(pi, ((b[1] - y0) / ysl, (b[0] - x0) / xsl))

    print("\n候选（独立页数 ≥3）：按页数排序，给出 y 位置中位数与离散度")
    print("  y≈0 贴下边  y≈1 贴上边  x 越小越靠左")
    rows = []
    for text, byp in stat.items():
        npages = len(byp)
        if npages < 3 or npages / pages < 0.03:
            continue
        ys = [v[0] for v in byp.values()]
        xs = [v[1] for v in byp.values()]
        rows.append((npages, statistics.median(ys), statistics.pstdev(ys) if len(ys) > 1 else 0.0,
                     statistics.median(xs), text))
    for npages, ymed, ysd, xmed, text in sorted(rows, reverse=True)[:20]:
        print(f"  {npages:3d}页({npages / pages:5.1%})  y={ymed:.2f}±{ysd:.2f}  x={xmed:.2f}  {text[:50]!r}")

    print("\n纯数字/罗马数字段（可能页码）——全部列出：")
    nums = [(len(byp), statistics.median([v[0] for v in byp.values()]),
             statistics.median([v[1] for v in byp.values()]), t)
            for t, byp in stat.items() if NUM.fullmatch(t.strip())]
    for npages, ymed, xmed, t in sorted(nums, reverse=True)[:15]:
        print(f"  {npages:3d}页  y={ymed:.2f}  x={xmed:.2f}  {t!r}")

    doc.close()


if __name__ == "__main__":
    main()
