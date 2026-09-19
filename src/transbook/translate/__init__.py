"""翻译层：Provider 抽象、批处理、提示词与编排。"""

from transbook.translate.base import (
    BookContext,
    SegmentIn,
    SegmentOut,
    TranslationProvider,
    Usage,
    estimate_tokens,
    make_batches,
)
from transbook.translate.deepseek import DeepSeekProvider, TranslationFormatError, parse_translations
from transbook.translate.fake import FakeProvider
from transbook.translate.prompts import PROMPT_VERSION, build_messages
from transbook.translate.runner import RunReport, run

__all__ = [
    "PROMPT_VERSION",
    "BookContext",
    "DeepSeekProvider",
    "FakeProvider",
    "RunReport",
    "SegmentIn",
    "SegmentOut",
    "TranslationFormatError",
    "TranslationProvider",
    "Usage",
    "build_messages",
    "estimate_tokens",
    "make_batches",
    "parse_translations",
    "run",
]
