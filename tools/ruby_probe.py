"""探测：竖排 PDF 的内联振假名（ruby）能否用字符高度区分。

用法：python tools/ruby_probe.py <pdf>
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pypdfium2 as pdfium  # noqa: E402

from transbook.ingest.pdf import char_height  # noqa: E402

PDF = sys.argv[1] if len(sys.argv) > 1 else ""
KANA = re.compile(r"[\u3041-\u309f\u30a1-\u30f6]")


def main() -> None:
    doc = pdfium.PdfDocument(PDF)
    total_small_kana = total_chars = 0
    shown = 0
    for pi in range(len(doc)):
        tp = doc[pi].get_textpage()
        raw = tp.get_text_range()
        n = tp.count_chars()
        boxes = []
        for i in range(n):
            try:
                boxes.append(tp.get_charbox(i))
            except Exception:  # noqa: BLE001
                boxes.append((0.0, 0.0, 0.0, 0.0))
        if not n:
            continue
        h = char_height(boxes)
        small = [i for i in range(n)
                 if 0 < (boxes[i][3] - boxes[i][1]) < h * 0.75 and KANA.match(raw[i])]
        total_small_kana += len(small)
        total_chars += n
        if small and shown < 2:
            shown += 1
            print("=== 第 %d 页 | 正文 h=%.1f | 小字假名 %d/%d ===" % (pi + 1, h, len(small), n))
            k = small[0]
            lo, hi = max(0, k - 8), min(n, k + 18)
            for i in range(lo, hi):
                b = boxes[i]
                hh = b[3] - b[1]
                print("   %4d %r h=%5.1f ratio=%.2f%s"
                      % (i, raw[i], hh, hh / h if h else 0,
                         "  ← 小字" if hh < h * 0.75 else ""))
    print("\n全书：小字假名 %d / 总字符 %d = %.1f%%"
          % (total_small_kana, total_chars,
             total_small_kana / max(total_chars, 1) * 100))
    doc.close()


if __name__ == "__main__":
    main()
