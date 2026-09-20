"""滚动摘要（M3）——长篇小说一致性最关键的一环。

问题：一部长篇里人名、称谓、伏笔跨章呼应，但翻译是**按批**做的，模型看不到前面。
术语表只能锁住固定词，锁不住"上一章谁死了、谁和谁结盟"。

做法：**从原文按章生成独立短摘要**（不是从译文——否则要先全译完才能摘要，
成了鸡生蛋）。翻译第 N 章时把"最近 K 章的前情"注入系统提示词。

之所以按章而不是整本：一是提示词预算有限，二是**每章只跑一次**，成本是章数而非段落数。

早期的"累积式"摘要（把新章合并进旧梗概）**已废弃**：实测让模型"合并并压缩到 N 字"
完全不可靠——要求 800 字，连跑十章涨到 3093 字，再加一轮"压缩"反而更长（见 D-052）。
现在改成"每章独立 + 注入时只取最近 K 章"，增长由"窗口 × 每章预算"确定性封顶。
唯一残留的风险是**模型不遵守单章字数上限**（那只是提示词里的要求，不是保证），
而注入时（`translate/runner.py::_prev_summary`）是把各章原样拼起来、不再裁剪——
所以对明显超标的章用 `compress_summary` 兜一次。
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field

from transbook.ir import Block, DocumentIR
from transbook.store import db as store
from transbook.translate.base import BookContext, TranslationProvider, Usage

#: 注入提示词的**总**字数上限
DEFAULT_BUDGET = 800
#: 注入最近多少章的摘要。总上限 ÷ 窗口 = 每章预算，增长天然有界。
DEFAULT_WINDOW = 4
#: 每章送进去的原文上限（字符）。摘要只需要情节要点，不需要全章。
DEFAULT_CHAPTER_CHARS = 6000
#: 参与前情提要的附页类别。目录/奥付/广告不含情节，写进梗概纯属噪声
#: （实测真实样书里 "CONTENTS" 与 "奥付" 都各自被当成了一章）。
SUMMARY_MATTER = ("main", "afterword")

SYSTEM = """你在为一部{source_lang}长篇小说的翻译做**前情提要**。
译者按章翻译，需要知道前几章发生了什么。要求：
1. 只保留**跨章仍然有用**的信息：人物及其称谓与关系、地点、关键事件、未解悬念、
   专有名词的既定译法。
2. 不要文学化描写、不要评论、不要逐条罗列——写成连贯的整段中文。
3. **不超过 {per_chapter} 字**（这是硬要求，超了会挤占正文的提示词预算）。
4. 只输出梗概本身，不要标题、不要解释。"""

USER = """请把第 {index} 章「{title}」的原文压缩成不超过 {per_chapter} 字的中文梗概。

原文：
{text}"""

EMPTY = "（这是第一章，没有前情）"

#: 单章摘要超过每章预算多少倍才值得再花一次调用去压。
#: 1.5 倍以内直接接受——为了几十个字再调一次 API 不划算，压缩本身也可能失败或变长。
OVERSIZE_FACTOR = 1.5

COMPRESS_SYSTEM = """你在压缩一份小说前情提要，供译者参考。要求：
1. 只保留**跨章仍然有用**的信息：人物及其称谓与关系、地点、关键事件、未解悬念、
   专有名词的既定译法。
2. 丢掉文学化描写、重复叙述、以及已经解决且不再相关的细节。
3. 写成连贯的整段中文，不要分点、不要标题。
4. 只输出压缩后的提要本身，不要解释你删了什么。"""

COMPRESS = """下面是一份前情提要，共 {n} 字，请压缩到**不超过 {budget} 字**。

硬要求：
- 必须不超过 {budget} 字。
- 保留人物、称谓、关系、关键事件与未解悬念。
- 写成连贯的整段中文，不要分点、不要评论。
- 只输出压缩后的提要本身。

原文：
{n_text}"""


@dataclass
class ChapterSpan:
    """一章覆盖的块范围。"""

    index: int
    title: str
    blocks: list[Block] = field(default_factory=list)

    def text(self, limit: int) -> str:
        return "\n".join(b.text for b in self.blocks if b.text.strip())[:limit]


def per_chapter_budget(budget: int, window: int) -> int:
    """每章摘要的字数预算（总上限 ÷ 窗口），并有下限兜底。"""
    return max(80, budget // max(1, window))


def chapter_spans(ir: DocumentIR, *,
                  matter: tuple[str, ...] = SUMMARY_MATTER) -> list[ChapterSpan]:
    """按标题块把全书切成章。

    切在**最上层标题**上（书里通常只有一级标题）；标题之前的块归入第 0 章"前言"。
    只收 `matter` 里的块：目录、奥付、广告不含情节，写进前情纯属噪声
    （实测真实样书里"CONTENTS""奥付"都被当成了一章）。

    章号可能因此不连续（被跳过的章不占号），下游用 `<` 比较取前情，不受影响。
    """
    heads = [b for b in ir.blocks if b.type == "heading"]
    top = min((b.level or 1) for b in heads) if heads else 1
    spans: list[ChapterSpan] = []
    cur = ChapterSpan(index=0, title="（前言）")
    counter = 0  # 独立计数：用 len(spans) 会在 append 之后跳号（踩过）
    for b in ir.blocks:
        if b.type == "heading" and (b.level or 1) <= top and b.text.strip():
            # **只有本身属于正文的标题才开一章**：封面/目次/奥付的标题不算章。
            # 只看标题层级的话，「表紙」「CONTENTS」会被当成章节去写摘要，
            # 还把夹在它们之间的版权页文字算进了「表紙」那一章（实测踩过）。
            if b.matter not in matter:
                continue
            if cur.blocks:
                spans.append(cur)
            counter += 1
            # 标题块自己也算本章成员：它是可翻译段，翻译时同样要按章取前情；
            # 漏掉它会让 `chapter_of_block` 查不到 → 注入错章（甚至空）
            cur = ChapterSpan(index=counter, title=b.text.strip(), blocks=[b])
            continue
        if b.is_translatable() and b.matter in matter:
            cur.blocks.append(b)
    if cur.blocks:
        spans.append(cur)
    return [s for s in spans if s.blocks]


def build_compress_messages(summary: str, *, budget: int) -> list[dict[str, str]]:
    """构造"把过长的前情提要压回预算内"的请求。

    为什么需要这一步：每章字数上限只是**提示词里的要求**，模型并不保证遵守；而注入
    前情时（`translate/runner.py::_prev_summary`）是把最近 K 章原样拼起来的，
    没有二次裁剪。某章写长了就会挤占正文的提示词预算，且随窗口逐批重复计费。
    """
    return [
        {"role": "system", "content": COMPRESS_SYSTEM},
        {"role": "user", "content": COMPRESS.format(
            n=len(summary), budget=budget, n_text=summary)},
    ]


def compress_summary(provider: TranslationProvider, summary: str, *,
                     budget: int) -> tuple[str, Usage]:
    """让模型把超长摘要压到 `budget` 字以内，返回 `(文本, usage)`。

    这里**不做成败判断**：调用失败、返回空、或压完反而更长，都由调用方决定怎么处理。
    摘要本身是可选增强，不该因为一次压缩不理想就丢掉已有内容。
    """
    return provider.complete(build_compress_messages(summary, budget=budget))


def build_summary_messages(span: ChapterSpan, *, source_lang: str = "ja",
                           per_chapter: int = 200,
                           chapter_chars: int = DEFAULT_CHAPTER_CHARS
                           ) -> list[dict[str, str]]:
    """构造**单章**摘要请求。

    刻意不做"把已有前情合并进来"的累积式请求：实测让模型"合并并压缩到 N 字"
    完全不可靠（要求 800 字，连跑十章涨到 3093 字，再加一轮"压缩"反而更长）。
    改成每章独立短摘要、注入时只取最近 K 章——增长**确定性有界**，
    不依赖模型守规矩。
    """
    system = SYSTEM.format(source_lang=source_lang, per_chapter=per_chapter)
    user = USER.format(index=span.index, title=span.title or "（无题）",
                       per_chapter=per_chapter,
                       text=span.text(chapter_chars) or "（本章无正文）")
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


@dataclass
class SummaryReport:
    chapters: int = 0
    generated: int = 0
    skipped: int = 0
    failed: int = 0
    pruned: int = 0
    compressed: int = 0
    usage: Usage = field(default_factory=Usage)

    def summary(self) -> str:
        tail = f" ｜ 清理过期 {self.pruned}" if self.pruned else ""
        comp = f" ｜ 压缩 {self.compressed}" if self.compressed else ""
        fail = f" ｜ [yellow]失败 {self.failed}[/yellow]" if self.failed else ""
        return (f"章节 {self.chapters} ｜ 新生成 {self.generated} ｜ 沿用已有 {self.skipped}"
                f"{tail}{comp}{fail} ｜ token 入 {self.usage.tokens_in:,} / "
                f"出 {self.usage.tokens_out:,} ｜ 花费 ¥{self.usage.cost:.4f}")


def generate_summaries(conn: sqlite3.Connection, provider: TranslationProvider,
                       ir: DocumentIR, ctx: BookContext, *,
                       budget: int = DEFAULT_BUDGET,
                       window: int = DEFAULT_WINDOW,
                       chapter_chars: int = DEFAULT_CHAPTER_CHARS,
                       force: bool = False,
                       progress: Callable[[str, float], None] | None = None) -> SummaryReport:
    """逐章生成**独立**短摘要并落库（每章一次调用，超标时可能再加一次压缩调用）。

    已生成过的章默认沿用（省钱、可断点续跑）。`force=True` 全部重生成。

    单章摘要若超过每章预算的 1.5 倍，会再调一次模型把它压下来（见 `compress_summary`）；
    压缩失败、返回空、或压完不更短时**保留原摘要**，绝不因为压缩不理想就丢内容。

    `progress` 的契约是 **`(message, fraction)` 两个参数**，与流水线其它阶段一致。
    曾经这里只传一个参数、而调用方传的是双参 lambda，第一次回调就抛 TypeError，
    结果整轮只生成了 1 章就中断——所以签名必须和别处统一。
    """
    spans = chapter_spans(ir)
    existing = store.summaries(conn, ir.doc.id)
    per_chapter = per_chapter_budget(budget, window)
    rep = SummaryReport(chapters=len(spans))
    total = max(1, len(spans))
    for i, span in enumerate(spans):
        frac = (i + 1) / total
        if not force and span.index in existing:
            rep.skipped += 1
            continue
        messages = build_summary_messages(span, source_lang=ctx.source_lang,
                                          per_chapter=per_chapter,
                                          chapter_chars=chapter_chars)
        try:
            text, usage = provider.complete(messages)
        except Exception as exc:  # noqa: BLE001
            # **一章失败不能拖垮整轮**：摘要本身是可选增强，某一章因网络或限流失败时
            # 应当跳过它继续后面的（重跑一次会自动补上缺的那章）。
            rep.failed += 1
            if progress:
                progress(f"第 {span.index} 章「{span.title}」摘要失败："
                         f"{type(exc).__name__}: {exc}", frac)
            continue
        summary = (text or "").strip()
        if not summary:
            rep.skipped += 1
            continue
        tokens_in, tokens_out, cost = usage.tokens_in, usage.tokens_out, usage.cost
        # 模型不一定遵守单章字数上限（那只是提示词里的**要求**），而注入前情时不再裁剪，
        # 所以明显超标就地压一次。阈值 1.5 倍：为几十个字再花一次调用不划算。
        if len(summary) > per_chapter * OVERSIZE_FACTOR:
            before = len(summary)
            shrunk, cusage = "", None
            try:
                shrunk, cusage = compress_summary(provider, summary, budget=per_chapter)
            except Exception as exc:  # noqa: BLE001
                # 压缩失败不该丢内容：保留原摘要继续，只是这一次没能压下来
                if progress:
                    progress(f"第 {span.index} 章摘要压缩失败（保留原样）："
                             f"{type(exc).__name__}", frac)
            shrunk = (shrunk or "").strip()
            # 只采用**确实更短**的结果——模型偶尔会越压越长（累积式摘要上实测过）
            if shrunk and len(shrunk) < before:
                summary = shrunk
                rep.compressed += 1
                if cusage is not None:
                    tokens_in += cusage.tokens_in
                    tokens_out += cusage.tokens_out
                    cost += cusage.cost
                    rep.usage.add(cusage)
        store.put_summary(conn, ir.doc.id, span.index, summary=summary, title=span.title,
                          engine=provider.name, model=provider.model,
                          tokens_in=tokens_in, tokens_out=tokens_out,
                          cost=cost)
        rep.generated += 1
        rep.usage.add(usage)
        if progress:
            progress(f"第 {span.index} 章「{span.title}」梗概 {len(summary)} 字", frac)
    # 分章规则可能变了（比如这次把目录/奥付排除掉），旧行必须清掉，
    # 否则 `summary_before` 会把陈旧摘要当成前情继续注入（实测踩过）
    rep.pruned = store.prune_summaries(conn, ir.doc.id, (s.index for s in spans))
    return rep


def chapter_of_block(ir: DocumentIR) -> dict[str, int]:
    """`block_id → 章序号`，供翻译时按批注入对应前情。"""
    out: dict[str, int] = {}
    for span in chapter_spans(ir):
        for b in span.blocks:
            out[b.id] = span.index
    return out
