"""翻译 Provider 抽象与批处理。

契约（对应 `docs/plan.md` §6.2/§6.3）：
* 以**段落为翻译单元**，成组批量发送；
* **稳定 ID 往返**：发出的每个 `seg_id` 必须原样回来，缺号/串号即视为错误并单独重试；
* 用量与成本随结果返回，供 `--max-cost` 硬护栏与成本报表使用。

Provider 与具体引擎解耦：DeepSeek API 与本地 OpenAI 兼容端点（llama.cpp / Ollama / vLLM）
都实现同一个 `translate()`。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class SegmentIn:
    """待译段落。"""

    seg_id: str
    text: str
    kind: str = "paragraph"


@dataclass
class SegmentOut:
    """一条译文结果；`error` 非空表示该段失败（会单独重试）。"""

    seg_id: str
    translation: str = ""
    error: str | None = None


@dataclass
class Usage:
    tokens_in: int = 0
    tokens_out: int = 0
    cost: float = 0.0
    calls: int = 0

    def add(self, other: "Usage") -> None:
        self.tokens_in += other.tokens_in
        self.tokens_out += other.tokens_out
        self.cost += other.cost
        self.calls += other.calls


@dataclass
class BookContext:
    """全书上下文：术语、滚动摘要与文体在每一批提示词里复用（也利于缓存命中）。"""

    doc_id: str = ""
    title: str = ""
    author: str = ""
    source_lang: str = "ja"
    target_lang: str = "zh"
    style_hint: str = "忠实、自然、书面语；人名与专有名词全书统一"
    glossary: dict[str, str] = field(default_factory=dict)
    do_not_translate: list[str] = field(default_factory=list)
    #: 已译章节的**滚动摘要**（M3）。长篇小说的人称/称谓/伏笔一致性靠它维持：
    #: 每译完一章就追加一段梗概，随后的批次都能看到前情。
    rolling_summary: str = ""

    def glossary_block(self) -> str:
        if not self.glossary:
            return "（无）"
        lines = [f"- {k} → {v}" for k, v in list(self.glossary.items())[:200]]
        return "\n".join(lines)


class TranslationProvider(ABC):
    """所有引擎的统一接口。"""

    name: str = "base"
    model: str = ""

    @abstractmethod
    def translate(self, items: list[SegmentIn], ctx: BookContext, *,
                  strict: bool = False) -> tuple[list[SegmentOut], Usage]:
        """翻译一批段落。返回 (结果列表, 用量)。结果必须覆盖全部输入 seg_id。

        `strict=True` 用于**未译重试**：提示词会追加"禁止原样返回原文"的强指令。
        """

    def estimate_cost(self, tokens_in: int, tokens_out: int) -> float:
        """按本引擎单价估算费用（元）。默认 0（本地模型 / 假引擎）。"""
        return 0.0


# ── 批处理 ──────────────────────────────────────────────────────────
DEFAULT_BATCH_CHARS = 2400
DEFAULT_BATCH_ITEMS = 24


def make_batches(items: list[SegmentIn], max_chars: int = DEFAULT_BATCH_CHARS,
                 max_items: int = DEFAULT_BATCH_ITEMS) -> list[list[SegmentIn]]:
    """按字符预算把段落切成批次（保持原顺序）。

    字符预算是提示词大小的代理指标：日文约 1~2 字符/token，2400 字符 ≈ 1.2~2.4k 输入 token，
    加上提示词与术语表仍在安全范围内，同时保持较少的请求次数。
    """
    batches: list[list[SegmentIn]] = []
    cur: list[SegmentIn] = []
    used = 0
    for it in items:
        n = len(it.text)
        if cur and (used + n > max_chars or len(cur) >= max_items):
            batches.append(cur)
            cur, used = [], 0
        cur.append(it)
        used += n
    if cur:
        batches.append(cur)
    return batches


def estimate_tokens(text: str) -> int:
    """保守估算 token 数（日文约 1.5 字符/token，中文更省）。"""
    return max(1, int(len(text) / 1.5))
