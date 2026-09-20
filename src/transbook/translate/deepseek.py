"""DeepSeek API Provider（OpenAI 兼容 /chat/completions）。

要点：
* **严格 JSON 往返**：要求模型返回 `{"translations":[{"id":..,"text":..}]}`，
  `seg_id` 原样返回才能对齐；解析失败会剥代码块、二次修复（由 runner 负责重试缺号）。
* **真实用量入账**：`usage` 直接取自 API 响应，成本按官方单价换算（默认按**高峰时段**保守估算，
  以免护栏失效）。
* **可注入 transport**：测试无需联网即可覆盖解析、修复与失败路径。
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable

from transbook.translate.base import (
    BookContext,
    SegmentIn,
    SegmentOut,
    TranslationProvider,
    Usage,
)
from transbook.translate.prompts import build_messages

DEFAULT_BASE_URL = "https://api.deepseek.com"

#: 元 / 百万 token，**空闲时段**单价（取自官方定价页
#: https://api-docs.deepseek.com/zh-cn/quick_start/pricing/）；高峰时段为其 2 倍。
PRICES: dict[str, dict[str, float]] = {
    "deepseek-flash": {"in_miss": 1.0, "in_hit": 0.02, "out": 4.0},
    "deepseek-v4-pro": {"in_miss": 4.5, "in_hit": 0.15, "out": 13.5},
}
#: 高峰时段 = 空闲 × 2（北京时间周一至周五 9-12、14-18，不含法定节假日）
PEAK_MULTIPLIER = 2.0

_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.MULTILINE)


def strip_fences(text: str) -> str:
    """去掉模型可能加上的 Markdown 代码块围栏。"""
    return _FENCE.sub("", text or "").strip()


def parse_translations(content: str) -> dict[str, str]:
    """解析模型输出为 {seg_id: 译文}；解析失败抛 ValueError。"""
    raw = strip_fences(content)
    data: Any = json.loads(raw)
    items = data.get("translations") if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise ValueError("输出不是 translations 数组")
    out: dict[str, str] = {}
    for it in items:
        if not isinstance(it, dict) or "id" not in it:
            continue
        out[str(it["id"])] = str(it.get("text", "") or "")
    return out


class TranslationFormatError(RuntimeError):
    """模型返回的结构无法解析。"""


class DeepSeekProvider(TranslationProvider):
    name = "deepseek"

    def __init__(
        self,
        api_key: str,
        model: str = "deepseek-flash",
        base_url: str = DEFAULT_BASE_URL,
        temperature: float = 0.2,
        price_tier: str = "peak",
        timeout: float = 180.0,
        transport: Callable[[list[dict[str, str]], str], tuple[str, dict[str, Any]]] | None = None,
        extra_body: dict[str, Any] | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("缺少 DeepSeek API key（请在 .env 里设置 DEEPSEEK_API_KEY）")
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.temperature = temperature
        self.price_tier = price_tier
        self.timeout = timeout
        self._transport = transport
        #: 引擎特有参数。**本地 Qwen3 必须传 `chat_template_kwargs.enable_thinking=False`**：
        #: 否则 token 全花在思考上，翻译会返回空内容（M0 实测踩过）。
        self.extra_body = extra_body or {}

    # ── 单价 ────────────────────────────────────────────────────
    def price_of(self) -> dict[str, float]:
        return PRICES.get(self.model, PRICES["deepseek-flash"])

    def estimate_cost(self, tokens_in: int, tokens_out: int) -> float:
        p = self.price_of()
        rate = PEAK_MULTIPLIER if self.price_tier == "peak" else 1.0
        return (tokens_in / 1e6 * p["in_miss"] * rate) + (tokens_out / 1e6 * p["out"] * rate)

    # ── 传输 ────────────────────────────────────────────────────
    def _http(self, messages: list[dict[str, str]], model: str) -> tuple[str, dict[str, Any]]:
        import httpx

        resp = httpx.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "messages": messages,
                "temperature": self.temperature,
                "response_format": {"type": "json_object"},
                **self.extra_body,
            },
            timeout=self.timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        return content, data.get("usage", {}) or {}

    def translate(self, items: list[SegmentIn], ctx: BookContext, *,
                  strict: bool = False) -> tuple[list[SegmentOut], Usage]:
        if not items:
            return [], Usage()
        messages = build_messages(items, ctx, strict=strict)
        send = self._transport or self._http
        content, raw_usage = send(messages, self.model)

        usage = Usage(
            tokens_in=int(raw_usage.get("prompt_tokens", 0) or 0),
            tokens_out=int(raw_usage.get("completion_tokens", 0) or 0),
            calls=1,
        )
        usage.cost = self.estimate_cost(usage.tokens_in, usage.tokens_out)
        if not usage.tokens_in:  # transport 未给用量时按估算（护栏不能失效）
            usage.tokens_in = sum(len(i.text) for i in items) // 2
            usage.tokens_out = usage.tokens_in
            usage.cost = self.estimate_cost(usage.tokens_in, usage.tokens_out)

        try:
            mapping = parse_translations(content)
        except Exception as exc:  # noqa: BLE001
            raise TranslationFormatError(f"无法解析模型输出: {exc}") from exc

        outs = [
            SegmentOut(it.seg_id, mapping[it.seg_id])
            if mapping.get(it.seg_id)
            else SegmentOut(it.seg_id, error="模型未返回该段落")
            for it in items
        ]
        return outs, usage
