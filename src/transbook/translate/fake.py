"""FakeProvider —— 零成本跑通全链路（对应计划书 §14.4 的调试手段）。

用途：
* 单元测试与端到端冒烟（**不花一分钱**）
* 在没有 API key、或在等模型下载时验证流水线
* 可注入错误（`fail_ids` / `fail_times`）以测试重试与失败标记
"""

from __future__ import annotations

from transbook.translate.base import BookContext, SegmentIn, SegmentOut, TranslationProvider, Usage


class FakeProvider(TranslationProvider):
    """返回 `[译]原文`，可选地让指定段落失败若干次。"""

    name = "fake"
    model = "fake-1"

    def __init__(self, prefix: str = "[译]", fail_ids: set[str] | None = None,
                 fail_times: int = 1, seed_translation: str | None = None) -> None:
        self.prefix = prefix
        self.fail_ids = fail_ids or set()
        self.fail_times = fail_times
        self.seed_translation = seed_translation
        self.calls: list[list[str]] = []
        self._failures: dict[str, int] = {}

    def translate(self, items: list[SegmentIn], ctx: BookContext) -> tuple[list[SegmentOut], Usage]:
        self.calls.append([i.seg_id for i in items])
        out: list[SegmentOut] = []
        for it in items:
            if it.seg_id in self.fail_ids:
                self._failures[it.seg_id] = self._failures.get(it.seg_id, 0) + 1
                if self._failures[it.seg_id] <= self.fail_times:
                    out.append(SegmentOut(it.seg_id, error="injected failure"))
                    continue
            text = self.seed_translation or f"{self.prefix}{it.text}"
            out.append(SegmentOut(it.seg_id, translation=text))
        return out, Usage(calls=1)
