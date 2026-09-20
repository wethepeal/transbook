"""探测：全书假名的高度分布，用来把"注音"与"促音/小假名"分开。

用法：python tools/ruby_hist.py <pdf>
"""

from __future__ import annotations

import collections
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pypdfium2 as pdfium  # noqa: E402

from transbook.ingest.pdf import char_height  # noqa: E402

PDF = sys.argv[1]
KANA = re.compile(r"[\u3041-\u309f\u30a1-\u30f6]")


def main() -> None:
    doc = pdfium.PdfDocument(PDF)
    hist: collections.Counter = collections.Counter()
    per_char: dict[str, list[float]] = collections.defaultdict(list)
    for pi in range(len(doc)):
        tp = doc[pi].get_textpage()
        raw = tp.get_text_range()
        n = tp.count_chars()
        if not n:
            continue
        boxes = []
        for i in range(n):
            try:
                boxes.append(tp.get_charbox(i))
            except Exception:  # noqa: BLE001
                boxes.append((0.0, 0.0, 0.0, 0.0))
        h = char_height(boxes)
        if h <= 0:
            continue
        for i in range(n):
            ch = raw[i]
            if not KANA.match(ch):
                continue
            hh = boxes[i][3] - boxes[i][1]
            if hh <= 0:
                continue
            r = hh / h
            hist[round(r, 1)] += 1
            if ch in "っッゃゅょャュョぁぃぅぇぉ":
                per_char[ch].append(r)
    print("假名字高比 (height / 页面正文中位数) 直方图：")
    for r in sorted(hist):
        bar = "#" * min(int(hist[r] / 400) + 1, 60)
        print("   %.1f  %6d  %s" % (r, hist[r], bar))
    print("\n小写/促音假名的比值分布：")
    for ch in sorted(per_char):
        vs = sorted(per_char[ch])
        med = vs[len(vs) // 2]
        print("   %r  n=%5d  中位 %.2f  最小 %.2f  最大 %.2f"
              % (ch, len(vs), med, vs[0], vs[-1]))
    doc.close()


if __name__ == "__main__":
    main()
