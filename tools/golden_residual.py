"""分析 PDF 抽取与 EPUB 真值的**剩余差异**，归类找系统性问题。

用法：python tools/golden_residual.py [pdf_work_dir]
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def paras(work: str) -> list[str]:
    ir = json.load(open(Path(work) / "book.ir.json", encoding="utf-8"))
    return [b["text"] for b in ir["blocks"]
            if b["type"] == "paragraph" and b["matter"] == "main"]


def norm(s: str) -> str:
    return re.sub(r"[\s\u3000]+", "", s)


epub = [norm(p) for p in paras("data/work/real-v43")]
pdf = [norm(p) for p in paras(sys.argv[1] if len(sys.argv) > 1 else "data/work/real-pdf43")]
se = set(epub)
only_pdf = [p for p in pdf if p not in se]
print("PDF 段 %d | 仅 PDF 有 %d" % (len(pdf), len(only_pdf)))

kind = Counter()
examples: dict[str, list[str]] = {}
for p in only_pdf:
    if any(p and p in e for e in epub):
        k = "是某 EPUB 段的子串（切得更碎）"
    elif len(p) <= 6:
        k = "极短段"
    elif re.search(r"[0-9０-９]", p) and len(p) <= 14:
        k = "短数字段"
    else:
        # 找最相似的 EPUB 段
        best, ratio = "", 0.0
        import difflib
        for e in epub:
            if abs(len(e) - len(p)) > 40:
                continue
            r = difflib.SequenceMatcher(None, p, e).quick_ratio()
            if r > ratio:
                best, ratio = e, r
        k = "近似段（相似度>0.8）" if ratio > 0.8 else "无对应段"
    kind[k] += 1
    examples.setdefault(k, []).append(p)

for k, n in kind.most_common():
    print("\n【%s】%d 个，示例：" % (k, n))
    for p in examples[k][:4]:
        print("   [%3d] %s" % (len(p), p[:96]))
