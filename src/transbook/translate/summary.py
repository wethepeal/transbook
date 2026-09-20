"""滚动摘要（M3）——长篇小说一致性最关键的一环。

问题：一部长篇里人名、称谓、伏笔跨章呼应，但翻译是**按批**做的，模型看不到前面。
术语表只能锁住固定词，锁不住"上一章谁死了、谁和谁结盟"。

做法：**从原文按章生成累积梗概**（不是从译文——否则要先全译完才能摘要，
成了鸡生蛋）。翻译第 N 章时把"截至第 N-1 章的前情"注入系统提示词。

之所以按章而不是整本：一是提示词预算有限，二是**每章只跑一次**，
第 N 章的梗概在前 N-1 章的基础上增量生成，成本是章数而非段落数。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Callable

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
            if cur.blocks:
                spans.append(cur)
            counter += 1
            # 标题块自己也算本章成员：它是可翻译段，翻译时同样要按章取前情；
            # 漏掉它会让 `chapter_of_block` 查不到 → 注入错章（甚至空）
            cur = ChapterSpan(index=counter, title=b.text.strip(),
                              blocks=[b] if b.matter in matter else [])
            continue
        if b.is_translatable() and b.matter in matter:
            cur.blocks.append(b)
    if cur.blocks:
        spans.append(cur)
    return [s for s in spans if s.blocks]


def build_compress_messages(summary: str, *, budget: int) -> list[dict[str, str]]:
    """超长时的压缩请求——不加这一步，累积梗概会一章比一章长。"""
    return [{"role": "user", "content": COMPRESS.format(
        n=len(summary), budget=budget, n_text=summary)}]


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
    pruned: int = 0
    usage: Usage = field(default_factory=Usage)

    def summary(self) -> str:
        tail = f" ｜ 清理过期 {self.pruned}" if self.pruned else ""
        return (f"章节 {self.chapters} ｜ 新生成 {self.generated} ｜ 沿用已有 {self.skipped}"
                f"{tail} ｜ token 入 {self.usage.tokens_in:,} / 出 {self.usage.tokens_out:,} ｜ "
                f"花费 ¥{self.usage.cost:.4f}")


def generate_summaries(conn: sqlite3.Connection, provider: TranslationProvider,
                       ir: DocumentIR, ctx: BookContext, *,
                       budget: int = DEFAULT_BUDGET,
                       window: int = DEFAULT_WINDOW,
                       chapter_chars: int = DEFAULT_CHAPTER_CHARS,
                       force: bool = False,
                       progress: Callable[[str], None] | None = None) -> SummaryReport:
    """逐章生成**独立**短摘要并落库（每章一次调用）。

    已生成过的章默认沿用（省钱、可断点续跑）。`force=True` 全部重生成。
    """
    spans = chapter_spans(ir)
    existing = store.summaries(conn, ir.doc.id)
    per_chapter = per_chapter_budget(budget, window)
    rep = SummaryReport(chapters=len(spans))
    for span in spans:
        if not force and span.index in existing:
            rep.skipped += 1
            continue
        messages = build_summary_messages(span, source_lang=ctx.source_lang,
                                          per_chapter=per_chapter,
                                          chapter_chars=chapter_chars)
        text, usage = provider.complete(messages)
        summary = (text or "").strip()
        if not summary:
            rep.skipped += 1
            continue
        store.put_summary(conn, ir.doc.id, span.index, summary=summary, title=span.title,
                          engine=provider.name, model=provider.model,
                          tokens_in=usage.tokens_in, tokens_out=usage.tokens_out,
                          cost=usage.cost)
        rep.generated += 1
        rep.usage.add(usage)
        if progress:
            progress(f"第 {span.index} 章「{span.title}」梗概 {len(summary)} 字")
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
