"""IR 数据模型。"""

from transbook.ir.models import (
    SCHEMA_VERSION,
    Block,
    DocMeta,
    DocumentIR,
    InlineSpan,
    TocEntry,
)

__all__ = ["SCHEMA_VERSION", "Block", "DocumentIR", "DocMeta", "InlineSpan", "TocEntry"]
