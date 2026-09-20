"""黄金比对：同一本书的 PDF 抽取 vs EPUB 抽取（EPUB 是真值），找出系统性差异。

用法：python tools/golden_diff.py
"""

from __future__ import annotations

import collections
import difflib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from transbook.textutil import normalize_ws  # noqa: E402


def paras(work: str) -> list[str]:
    ir = json.load(open(Path(work) / "book.ir.json", encoding="utf-8"))
    return [b["text"] for b in ir["blocks"]
            if b["type"] == "paragraph" and b["matter"] == "main"]


def norm(s: str) -> str:
    return re.sub(r"[\s\u3000]+", "", s)


epub = [norm(p) for p in paras("data/work/real-v43")]
pdf = [norm(p) for p in paras(sys.argv[1] if len(sys.argv) > 1 else "data/work/real-pdf43")]
print("EPUB(真值) %d 段 | PDF %d 段" % (len(epub), len(pdf)))

se = set(epub)
sp = set(pdf)
print("精确相同：PDF 段命中 EPUB %d/%d = %.1f%%"
      % (len(sp & se), len(sp), len(sp & se) / len(sp) * 100))
print("EPUB 段被 PDF 覆盖 %d/%d = %.1f%%"
      % (len(se & sp), len(se), len(se & sp) / len(se) * 100))

# 前缀/包含关系（PDF 常把段落切得更碎）
frag = [p for p in pdf if p not in se and any(p in e for e in epub)]
print("PDF 有但 EPUB 无的段 %d，其中是某 EPUB 段之子串的 %d"
      % (len(sp - se), len(frag)))

# 逐段近似对齐，统计相似度分布
sm = difflib.SequenceMatcher(None, "\n".join(epub), "\n".join(pdf))
print("整书字符级相似度 %.4f" % sm.ratio())

# PDF 里"很短"的段（可能的碎片）
short = [p for p in pdf if len(p) <= 8]
print("\nPDF 中长度≤8 的段 %d 个（EPUB 中 %d 个）"
      % (len(short), len([p for p in epub if len(p) <= 8])))
for p in short[:12]:
    print("   %r" % p)

# 疑似拉丁/数字碎片（縦中横、旋转字可能被拆）
lat = [p for p in pdf if 1 <= len(p) <= 3 and re.fullmatch(r"[0-9A-Za-z０-９]{1,3}", p)]
print("\nPDF 中 1~3 字符的纯拉丁/数字段 %d 个：" % len(lat))
c = collections.Counter(lat)
for t, n in c.most_common(12):
    print("   %-6r %d 次" % (t, n))

# 未在 EPUB 出现的 PDF 段里，最长的几个（可能是 PDF 特有的噪声）
only_pdf = sorted((sp - se), key=len, reverse=True)
print("\n仅 PDF 有的段（最长 5 个）：")
for p in only_pdf[:5]:
    print("   [%4d] %s" % (len(p), p[:110]))
