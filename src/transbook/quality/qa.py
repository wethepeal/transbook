"""译文质量自动检查（QA）。

对应计划书 §14.6 的"最重要的自动闸门"：机翻难免出错，**必须在渲染前自动把关**，
而不是等读者发现。检查项都是可判定的客观规则：

| 代号 | 含义 | 级别 |
|---|---|---|
| `id_mismatch` | IR 里的可翻译块在段落表中找不到（结构/入库不一致） | error |
| `untranslated` | 没有译文（pending / failed） | error |
| `source_leak` | 译文与原文完全相同（没翻） | error |
| `kana_left` | 中文译文里残留**平假名**（几乎必然是漏译） | error |
| `term_missing` | 原文出现了术语表词条，但译文里没有对应译法（一致性违规） | error |
| `katakana_left` | 译文里残留片假名（可能是刻意保留的专名，故只警告） | warn |
| `length_anomaly` | 译/原长度比异常（可能漏译或串段） | warn |
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field

from transbook.ir import DocumentIR
from transbook.textutil import is_untranslated

HIRAGANA = re.compile(r"[\u3041-\u309f]")
KATAKANA = re.compile(r"[\u30a1-\u30f6]")
#: 假名（平/片）——用于判断"是否真的需要翻译"
KANA = re.compile(r"[\u3041-\u309f\u30a1-\u30f6]")
#: **非简体**汉字 = 繁体专用字 + 日文专用汉字。
#: 中文译文里成片出现，即是繁体输出或残留日文汉字——实测本地 Qwen3-8B 整段输出
#: `一章『氷上決戰』`「異常事態」`那是他自認應該承擔的角色`，而这类问题
#: **不含假名**，原来的"假名残留"检查完全抓不到。
NON_SIMPLIFIED = frozenset(
    "們這說時會對應該與學樣麼為國實際發現覺讓從來過開關門問間種見語話讀寫戰軍"
    "隊將領點兒幾頭體認識記號產業務動區醫藥書車馬鳥魚龍風飛長東樂買賣錢銀鐵銅"
    "錯題聽習經歷陽陰燈樹橋樓驚嚇媽愛願夢麗歡溫凍淨準確態總縣鄉萬億豐歲"
    "紅綠藍黃筆紙張亂舊觀覽權議講論誰討謝遠邊靜盡層屬榮嚴寶獻"
    # 日文专用汉字（简体与繁体都不用）
    "氷駅沢浜畑峠辻込働榊畠嶋瀬麿凪雫咲"
)
# 注意：`姐妹暖黑` 这类字**简繁同形**，放进集合会制造误报（实测在真实成品上
# 报了 5 条假警）。改动集合后务必跑 `tests/test_compare.py` 里的简繁回归测试。
_WS = re.compile(r"[\s\u3000]+")

ERROR, WARN = "error", "warn"


@dataclass
class QaIssue:
    kind: str
    seg_id: str
    detail: str
    severity: str = ERROR

    def __str__(self) -> str:
        tag = "✗" if self.severity == ERROR else "!"
        return f"  {tag} [{self.kind}] {self.seg_id}: {self.detail}"


@dataclass
class QaReport:
    checked: int = 0
    issues: list[QaIssue] = field(default_factory=list)

    def add(self, *a, **kw) -> None:
        self.issues.append(QaIssue(*a, **kw))

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for i in self.issues:
            out[i.kind] = out.get(i.kind, 0) + 1
        return out

    @property
    def errors(self) -> list[QaIssue]:
        return [i for i in self.issues if i.severity == ERROR]

    @property
    def warnings(self) -> list[QaIssue]:
        return [i for i in self.issues if i.severity == WARN]

    def ok(self) -> bool:
        return not self.errors

    def summary(self) -> str:
        c = self.counts()
        if not c:
            return f"检查 {self.checked} 段：全部通过 ✅"
        parts = " ｜ ".join(f"{k} {v}" for k, v in sorted(c.items(), key=lambda x: -x[1]))
        return f"检查 {self.checked} 段：错误 {len(self.errors)} ｜ 警告 {len(self.warnings)}（{parts}）"


def _norm(s: str) -> str:
    return _WS.sub("", s or "")


def check(conn: sqlite3.Connection, ir: DocumentIR | None = None,
          glossary: dict[str, str] | None = None, *, min_ratio: float = 0.12,
          max_ratio: float = 3.0, max_examples: int = 0,
          min_source_len: int = 6) -> QaReport:
    """对段落表跑一轮 QA。`ir` 提供结构对齐校验；`glossary` 提供一致性校验。

    `min_source_len`：太短的原文（「１」「──」「あ」）长度比不稳定，会制造噪声，
    默认不参与长度检查（真实样书上这类误报占 length_anomaly 的大头）。
    """
    rep = QaReport()
    rows = list(conn.execute(
        "SELECT seg_id, block_id, source_text, translation, final_translation, status "
        "FROM segment ORDER BY doc_id, ord"
    ))
    rep.checked = len(rows)

    # ① 结构对齐：IR 的可翻译块都应在段落表里
    if ir is not None:
        known = {r["block_id"] for r in rows}
        for b in ir.translatable():
            if b.id not in known:
                rep.add("id_mismatch", b.id, "IR 中的可翻译块在段落表中不存在")

    for r in rows:
        src = r["source_text"] or ""
        tgt = (r["final_translation"] or r["translation"] or "").strip()
        seg = r["seg_id"]

        # ② 未译 / 失败
        if not tgt:
            rep.add("untranslated", seg,
                    f"状态 {r['status']}，无译文", WARN if r["status"] == "pending" else ERROR)
            continue

        # ③ 原文泄漏（等于没翻）。
        #    只对**含假名**的原文报警：纯汉字/符号串（「第七章 『Reweave』」「「────」」）
        #    译成同样内容是正确的，早期版本会误报 115 条（真实数据驱动修正）。
        #    与翻译时的**未译护栏**共用同一个判定（`textutil.is_untranslated`），
        #    避免"护栏认为没翻、QA 认为没问题"这种口径分裂。
        if _norm(tgt) == _norm(src):
            if is_untranslated(src, tgt):
                rep.add("source_leak", seg, f"译文与原文相同：{src[:30]!r}")
            continue

        # ④ 残留假名
        hir = HIRAGANA.findall(tgt)
        if hir:
            rep.add("kana_left", seg, f"译文残留平假名 {''.join(hir[:6])!r}：{tgt[:40]!r}")
        elif KATAKANA.search(tgt):
            rep.add("katakana_left", seg, f"译文残留片假名：{tgt[:40]!r}", WARN)

        # ④b 繁体字 / 残留日文汉字。阈值 2：单个繁体字可能是刻意保留的人名用字。
        trad = sorted({c for c in tgt if c in NON_SIMPLIFIED})
        if len(trad) >= 2:
            rep.add("traditional", seg,
                    f"译文疑似繁体或残留日文汉字 {''.join(trad[:8])!r}：{tgt[:40]!r}", WARN)

        # ⑤ 术语一致性
        for term_src, term_tgt in (glossary or {}).items():
            if term_src and term_src in src and term_tgt and term_tgt not in tgt:
                rep.add("term_missing", seg,
                        f"术语 {term_src!r} 应译为 {term_tgt!r}，但译文中未出现")
                break

        # ⑥ 长度异常（日→中通常 0.5~1.2；过短原文不参与，避免噪声）
        src_len = len(_norm(src))
        if src_len >= min_source_len:
            ratio = len(_norm(tgt)) / src_len
            if ratio < min_ratio or ratio > max_ratio:
                rep.add("length_anomaly", seg,
                        f"译/原长度比 {ratio:.2f}（原文 {len(src)} 字 → 译文 {len(tgt)} 字）", WARN)

    if max_examples and len(rep.issues) > max_examples:
        rep.issues = rep.issues[:max_examples]
    return rep
