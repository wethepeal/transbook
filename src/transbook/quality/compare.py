"""引擎对比（M3）——同一批段落跑两个引擎，给出可判读的差异报告。

为什么要有这个：计划书要求"同章 API vs 本地对比报告；术语一致率量化；
单本成本 < ¥25"。没有对比就没法决定"哪些章可以用本地模型省下来"。

报告给三类东西：
1. **成本与速度**：token、耗时、每条单价换算；
2. **可判定的质量指标**：残留假名、未译（=原文）、长度比异常、术语缺失——
   复用 `tp qa` 的判定，不引入主观评分；
3. **逐条对照样本**：只给前若干条，供人眼判断"本地模型到底能不能用"。
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

from transbook.textutil import is_untranslated, normalize_ws
from transbook.translate.base import (
    BookContext,
    SegmentIn,
    TranslationProvider,
    Usage,
    estimate_tokens,
    make_batches,
)

HIRAGANA = re.compile(r"[\u3041-\u309f]")
KATAKANA = re.compile(r"[\u30a1-\u30f6]")


@dataclass
class EngineResult:
    name: str
    model: str = ""
    seconds: float = 0.0
    usage: Usage = field(default_factory=Usage)
    translations: dict[str, str] = field(default_factory=dict)
    error: str = ""
    #: 逐段判定出来的问题数
    issues: dict[str, int] = field(default_factory=dict)

    @property
    def chars(self) -> int:
        return sum(len(v) for v in self.translations.values())

    def per_million_chars(self) -> float:
        """换算成"每百万源字符"的成本，便于跨规模比较。"""
        n = sum(len(v) for v in self.translations.values())
        return self.usage.cost / n * 1e6 if n else 0.0


@dataclass
class CompareReport:
    results: list[EngineResult] = field(default_factory=list)
    samples: list[tuple[str, str, dict[str, str]]] = field(default_factory=list)

    def by_name(self, name: str) -> EngineResult | None:
        return next((r for r in self.results if r.name == name), None)

    def table(self) -> str:
        head = (f"{'引擎':<12}{'耗时(s)':>9}{'源字符':>9}{'输出字符':>9}"
                f"{'token入':>10}{'token出':>9}{'花费¥':>10}{'字符/s':>9}")
        rows = [head, "-" * len(head)]
        for r in self.results:
            speed = r.chars / r.seconds if r.seconds else 0.0
            rows.append(f"{r.name:<12}{r.seconds:>9.1f}{sum(len(v) for v in r.translations.values()):>9}"
                        f"{r.chars:>9}{r.usage.tokens_in:>10,}{r.usage.tokens_out:>9,}"
                        f"{r.usage.cost:>10.4f}{speed:>9.1f}")
        return "\n".join(rows)


def _judge(src: str, tgt: str, glossary: dict[str, str]) -> list[str]:
    """逐段可判定问题（与 `tp qa` 同源思路，故结论可比）。"""
    out: list[str] = []
    if not tgt.strip():
        return ["empty"]
    if is_untranslated(src, tgt):
        out.append("untranslated")
    if HIRAGANA.findall(tgt):
        out.append("kana_left")
    elif KATAKANA.search(tgt):
        out.append("katakana_left")
    s, t = normalize_ws(src), normalize_ws(tgt)
    if len(s) >= 6:
        ratio = len(t) / max(len(s), 1)
        if ratio < 0.12 or ratio > 3.0:
            out.append("length_anomaly")
    for k, v in (glossary or {}).items():
        if k and k in src and v and v not in tgt:
            out.append("term_missing")
            break
    from transbook.quality.qa import NON_SIMPLIFIED

    if len({c for c in tgt if c in NON_SIMPLIFIED}) >= 2:
        out.append("traditional")
    return out


def run_engine(provider: TranslationProvider, items: list[SegmentIn],
               ctx: BookContext, *, batch_chars: int = 2400, batch_items: int = 24,
               max_rounds: int = 2, progress=None) -> EngineResult:
    """把同一批段落完整跑一遍某个引擎（含缺号重试）。"""
    res = EngineResult(name=provider.name, model=getattr(provider, "model", "") or "")
    batches = make_batches(items, max_chars=batch_chars, max_items=batch_items)
    started = time.perf_counter()
    for bi, batch in enumerate(batches, start=1):
        pending = list(batch)
        if progress:
            progress(f"[{res.name}] 批次 {bi}/{len(batches)}")
        for _ in range(max_rounds):
            if not pending:
                break
            try:
                outs, usage = provider.translate(pending, ctx)
            except Exception as exc:  # noqa: BLE001 - 对比工具不该因为一个引擎崩掉就整体失败
                res.error = f"{type(exc).__name__}: {exc}"[:200]
                break
            res.usage.add(usage)
            retry = []
            for o in outs:
                if o.error:
                    retry.append(next(i for i in pending if i.seg_id == o.seg_id))
                elif o.translation.strip():
                    res.translations[o.seg_id] = o.translation
                else:
                    retry.append(next(i for i in pending if i.seg_id == o.seg_id))
            pending = retry
        for it in pending:
            res.translations.setdefault(it.seg_id, "")
    res.seconds = time.perf_counter() - started

    src_of = {i.seg_id: i.text for i in items}
    counter: dict[str, int] = {}
    for seg_id, tgt in res.translations.items():
        for kind in _judge(src_of.get(seg_id, ""), tgt, ctx.glossary):
            counter[kind] = counter.get(kind, 0) + 1
    res.issues = counter
    if not res.usage.tokens_in:  # 本地端点常不给用量，用估算兜底
        res.usage.tokens_in = sum(estimate_tokens(i.text) for i in items)
        res.usage.tokens_out = sum(estimate_tokens(v) for v in res.translations.values())
    return res


def compare(items: list[SegmentIn], providers: list[TranslationProvider],
            ctx: BookContext, *, sample: int = 5, progress=None, **kw) -> CompareReport:
    """依次跑各引擎并汇总。逐条样本用于人眼判断，指标用于量化。"""
    rep = CompareReport()
    for p in providers:
        rep.results.append(run_engine(p, items, ctx, progress=progress, **kw))
    for it in items[:sample]:
        rep.samples.append((it.seg_id, it.text,
                            {r.name: r.translations.get(it.seg_id, "") for r in rep.results}))
    return rep
