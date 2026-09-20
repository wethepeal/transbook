"""含空格敏感的黄金比对：PDF 抽取 vs EPUB 真值，逐字符完全一致的比例。

`golden_diff.py` 会先去掉所有空白，对空格不敏感；本脚本不做任何归一化，
用来量化「PDF 特有的空格」这类噪声还剩多少。

用法：python tools/exact_diff.py [pdf_work_dir]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def paras(work: str) -> list[str]:
    ir = json.load(open(Path(work) / "book.ir.json", encoding="utf-8"))
    return [b["text"] for b in ir["blocks"]
            if b["type"] == "paragraph" and b["matter"] == "main"]


epub = paras("data/work/real-v43")
pdf = paras(sys.argv[1] if len(sys.argv) > 1 else "data/work/real-pdf43")
se = set(epub)
hit = sum(1 for p in pdf if p in se)
print("逐字符完全一致（不去空白）：%d/%d = %.1f%%" % (hit, len(pdf), hit / len(pdf) * 100))

# 还剩多少段含"两侧都是 CJK 的空格"
import re  # noqa: E402

CJK = (r"\u3000-\u303f\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff"
       r"\uf900-\ufaff\ufe30-\ufe4f\uff00-\uff60\uffe0-\uffe6"
       r"\u2010-\u2015\u2025\u2026\u2500-\u257f")
pat = re.compile(f"(?<=[{CJK}])[ \\u3000]+(?=[{CJK}])")
bad = [p for p in pdf if pat.search(p)]
print("仍含 CJK 间空格的段：%d（%.1f%%）" % (len(bad), len(bad) / max(len(pdf), 1) * 100))
for p in bad[:4]:
    print("   %r" % p[:70])
