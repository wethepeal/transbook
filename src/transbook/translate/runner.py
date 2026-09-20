"""翻译流水线编排：断点续跑 + 缺号重试 + 成本硬护栏 + 干跑预估。

设计（计划书 §6.3 与 §14.4）：
* **可续跑**：只取 `pending/failed` 段落；每批成功即落库，中断后重跑不重复计费；
* **缺号重试**：一批里模型漏返回的段落，收拢成更小的批次重试（最多 `max_rounds` 轮），
  仍失败则标记 `failed`，供 `tp retry` 单独处理；
* **硬护栏**：累计成本达到 `max_cost` 立即停止并保存进度（默认测试 ¥0.5 / 正式 ¥25）；
* **干跑**：只做分批与费用预估，**不调用任何 API**（花钱前先看价）。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Callable

from transbook.store import db as store
from transbook.textutil import is_untranslated
from transbook.translate.base import (
    BookContext,
    SegmentIn,
    TranslationProvider,
    Usage,
    estimate_tokens,
    make_batches,
)
from transbook.translate.prompts import PROMPT_VERSION

#: 未译护栏的生效下限：原文短于此长度就放过。
#: `「べ」` 这类拟声片段原样保留无可厚非，拿它去重试纯属浪费 token。
GUARD_MIN_CHARS = 20


@dataclass
class RunReport:
    dry_run: bool = False
    batches: int = 0
    translated: int = 0
    failed: int = 0
    retried: int = 0
    #: 被护栏判定为"原样返回原文"并触发强指令重试的段落数（M3）
    guarded: int = 0
    #: 重试后仍然是原文的段落数——已保留译文并交由 `tp qa` 复核
    still_untranslated: int = 0
    usage: Usage = field(default_factory=Usage)
    stopped: str = ""
    est_tokens_in: int = 0
    est_tokens_out: int = 0
    est_cost: float = 0.0

    def summary(self) -> str:
        if self.dry_run:
            return (f"[干跑] 批次 {self.batches} ｜ 预估输入 {self.est_tokens_in:,} token ｜ "
                    f"输出 {self.est_tokens_out:,} token ｜ 预估费用 ¥{self.est_cost:.4f}"
                    f"{'（' + self.stopped + '）' if self.stopped else ''}")
        extra = ""
        if self.guarded:
            extra = f" ｜ 未译护栏 {self.guarded}"
            if self.still_untranslated:
                extra += f"（仍原样 {self.still_untranslated}）"
        return (f"批次 {self.batches} ｜ 已译 {self.translated} ｜ 失败 {self.failed} ｜ "
                f"重试补齐 {self.retried} ｜ token 入 {self.usage.tokens_in:,} / 出 {self.usage.tokens_out:,} ｜ "
                f"花费 ¥{self.usage.cost:.4f}{extra}"
                f"{' ｜ 停止原因: ' + self.stopped if self.stopped else ''}")


def run(
    conn: sqlite3.Connection,
    provider: TranslationProvider,
    ctx: BookContext,
    *,
    limit: int | None = None,
    max_cost: float = 0.0,
    batch_chars: int = 2400,
    batch_items: int = 24,
    max_rounds: int = 3,
    dry_run: bool = False,
    progress: Callable[[str, float], None] | None = None,
    guard_min_chars: int = GUARD_MIN_CHARS,
    ir=None,
    rolling_summary: bool = False,
    summary_window: int = 4,
) -> RunReport:
    """执行（或干跑）翻译。

    `ir` + `rolling_summary=True` 时，按**批次所属的章**注入"最近 `summary_window`
    章的前情"（`tp summarize` 生成，存在 `chapter_summary` 表里），
    用来压住长篇的人称/称谓/伏笔漂移。干跑会一并给出注入规模，便于先看价。
    """
    rows = store.pending(conn, limit)
    items = [SegmentIn(r["seg_id"], r["source_text"], r["kind"]) for r in rows]
    batches = make_batches(items, max_chars=batch_chars, max_items=batch_items)
    rep = RunReport(dry_run=dry_run, batches=len(batches))

    # 前情注入表：{章号: 累积梗概} + {block_id: 章号}
    summaries: dict[int, str] = {}
    chap_of: dict[str, int] = {}
    if rolling_summary:
        if ir is None:
            raise ValueError("rolling_summary 需要同时传入 ir（用于定位每批所属的章）")
        from transbook.translate.summary import chapter_of_block

        summaries = store.summaries_with_titles(conn, ir.doc.id)
        chap_of = chapter_of_block(ir)

    def _prev_summary(seg_ids: list[str]) -> str:
        """本批所属章的**前情**：最近 `summary_window` 章（严格早于本章）的摘要。"""
        if not summaries or not seg_ids:
            return ""
        unit = seg_ids[0].split(":", 1)[1]
        ci = chap_of.get(unit.split(":", 1)[0], 1)
        keys = [k for k in sorted(summaries) if k < ci][-max(1, summary_window):]
        if not keys:
            return ""
        parts = [f"第 {k} 章「{summaries[k][1]}」：{summaries[k][0]}" for k in keys]
        return "前情提要：\n" + "\n".join(parts)

    if dry_run:
        rep.est_tokens_in = sum(estimate_tokens(i.text) for i in items)
        # 提示词与术语表开销按 1.4 倍估；输出按源文的 0.8 倍估（中文更紧凑）
        rep.est_tokens_in = int(rep.est_tokens_in * 1.4)
        if summaries:
            # 前情是**每批**都要重复注入的固定前缀，按批数计费
            extra = sum(estimate_tokens(_prev_summary([i.seg_id for i in batch]))
                        for batch in batches)
            rep.est_tokens_in += extra
        rep.est_tokens_out = int(rep.est_tokens_in * 0.7)
        rep.est_cost = provider.estimate_cost(rep.est_tokens_in, rep.est_tokens_out)
        if max_cost and rep.est_cost > max_cost:
            rep.stopped = f"预估已超上限 ¥{max_cost}"
        return rep

    for bi, batch in enumerate(batches, start=1):
        if max_cost and rep.usage.cost >= max_cost:
            rep.stopped = f"已达成本上限 ¥{max_cost}"
            break
        if progress:
            # 回调带**批次占比**：上层（服务/流水线）要把它映射进所属阶段的区间，
            # 否则嵌套阶段会让进度条倒退（实测 2% → 0% → 92%）。
            progress(f"批次 {bi}/{len(batches)}（{len(batch)} 段）", bi / len(batches))
        if summaries:
            ctx.rolling_summary = _prev_summary([i.seg_id for i in batch])

        outs, usage = _translate_batch_with_repair(provider, ctx, batch, max_rounds, rep,
                                                   guard_min_chars=guard_min_chars)
        rep.usage.add(usage)

        for out in outs:
            if out.error:
                store.record_failure(conn, out.seg_id, out.error)
                rep.failed += 1
            else:
                store.record_translation(
                    conn, out.seg_id, out.translation,
                    engine=provider.name, model=provider.model,
                    prompt_version=PROMPT_VERSION,
                    tokens_in=usage.tokens_in // max(len(batch), 1),
                    tokens_out=usage.tokens_out // max(len(batch), 1),
                    cost=usage.cost / max(len(batch), 1),
                )
                rep.translated += 1

    if not rep.stopped and max_cost and rep.usage.cost >= max_cost:
        rep.stopped = f"已达成本上限 ¥{max_cost}"
    return rep


def _translate_batch_with_repair(
    provider: TranslationProvider,
    ctx: BookContext,
    batch: list[SegmentIn],
    max_rounds: int,
    rep: RunReport,
    *,
    guard_min_chars: int = GUARD_MIN_CHARS,
) -> tuple[list, Usage]:
    """翻译一批；对缺号/失败/原样返回原文的段落收拢重试。

    **未译护栏**（M3）：模型偶发把长段原文原样返回。这类问题不会报错、状态还是
    `done`，只有 QA 事后能看出来，所以我们在这里就地重试——而且**必须换成强指令**，
    原样重发同样的请求大概率得到同样的结果（`strict=True`）。
    """
    total = Usage()
    pending_items = list(batch)
    results: dict[str, object] = {}
    strict = False

    for attempt in range(max_rounds):
        if not pending_items:
            break
        try:
            outs, usage = provider.translate(pending_items, ctx, strict=strict)
        except Exception as exc:  # noqa: BLE001 - 网络/解析错误统一降级为该批失败
            for it in pending_items:
                results[it.seg_id] = _err(it.seg_id, f"{type(exc).__name__}: {exc}")
            break
        total.add(usage)
        if attempt:
            rep.retried += sum(1 for o in outs if not o.error)

        src_of = {i.seg_id: i.text for i in pending_items}
        retry: list[SegmentIn] = []
        untranslated_retry = 0
        for out in outs:
            if out.error:
                retry.append(next(i for i in pending_items if i.seg_id == out.seg_id))
                results[out.seg_id] = out
                continue
            if is_untranslated(src_of[out.seg_id], out.translation,
                               min_chars=guard_min_chars):
                retry.append(next(i for i in pending_items if i.seg_id == out.seg_id))
                # 先留着这份译文：万一重试也没修好，至少不会把段落弄成"空"
                results[out.seg_id] = out
                untranslated_retry += 1
            else:
                results[out.seg_id] = out
        if untranslated_retry:
            rep.guarded += untranslated_retry
            strict = True
        pending_items = retry
        if not pending_items:
            break

    for it in pending_items:
        out = results.get(it.seg_id)
        if out is None:
            results[it.seg_id] = _err(it.seg_id, "重试后仍未返回")
        elif getattr(out, "error", None) is None:
            # 重试后仍是原文 → 保留译文，但记账让报告与 QA 能看见
            rep.still_untranslated += 1

    ordered = [results[i.seg_id] for i in batch if i.seg_id in results]
    return ordered, total


def _err(seg_id: str, msg: str):
    from transbook.translate.base import SegmentOut

    return SegmentOut(seg_id, error=msg)
