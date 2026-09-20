"""探测 PDF 的脚注：字号明显小于正文、且位于页面下部的段。

用法：python tools/footnote_probe.py <pdf>
只读统计。
"""

from __future__ import annotations

import collections
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pypdfium2 as pdfium  # noqa: E402

from transbook.ingest.pdf import char_height, detect_vertical  # noqa: E402
from transbook.textutil import normalize_ws  # noqa: E402


def main() -> None:
    path = Path(sys.argv[1])
    doc = pdfium.PdfDocument(path)
    pages = len(doc)
    print(f"{path.name}  页数 {pages}")

    allh: list[float] = []
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
        h = char_height(boxes)
        segs = []
        pos = 0
        for part in raw.split("\r\n"):
            if part.strip():
                first = pos
                for j, ch in enumerate(part):
                    if ch.strip():
                        first = pos + j
                        break
                if first < len(boxes):
                    b = boxes[first]
                    segs.append((normalize_ws(part), b, b[3] - b[1]))
            pos += len(part) + 2
        allh.extend(s[2] for s in segs if s[2] > 0.5)
        per_page.append((h, segs, boxes))

    body_h = statistics.median(allh) if allh else 12.0
    print("全书段首字符高度中位数 %.1f" % body_h)

    small = collections.Counter()
    for pi, (h, segs, boxes) in enumerate(per_page):
        ys = [b[1] for b in boxes]
        if not ys:
            continue
        ylo, yhi = min(ys), max(ys)
        span = (yhi - ylo) or 1.0
        for t, b, sh in segs:
            if sh < body_h * 0.85 and t:
                posn = (b[1] - ylo) / span
                small[round(posn, 1)] += 1
    print("\n小于正文 85% 的段，按页面纵向位置分布（0=底部 1=顶部）：")
    for p, n in sorted(small.items()):
        print("   pos=%.1f  %d 段" % (p, n))

    print("\n小字号段示例（前 12，含所在页与位置）：")
    shown = 0
    for pi, (h, segs, boxes) in enumerate(per_page):
        ys = [b[1] for b in boxes]
        if not ys:
            continue
        ylo, yhi = min(ys), max(ys)
        span = (yhi - ylo) or 1.0
        for t, b, sh in segs:
            if sh < body_h * 0.85 and t and shown < 12:
                print("   p%-4d h=%4.1f pos=%.2f %r" % (pi + 1, sh, (b[1] - ylo) / span, t[:60]))
                shown += 1
    doc.close()


if __name__ == "__main__":
    main()
