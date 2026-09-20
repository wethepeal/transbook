"""输入适配层：把 EPUB / PDF 抽取成统一的 DocumentIR。"""

from transbook.ingest.epub import EpubError, EpubIngestor
from transbook.ingest.pdf import (
    PdfError,
    PdfIngestor,
    detect_vertical,
    segment_kind,
    segments_to_paragraphs,
)


def ingestor_for(path, doc_id: str | None = None):
    """按扩展名选择抽取器（EPUB 优先——结构由作者标注，质量天然高于 PDF 版面推断）。"""
    from pathlib import Path

    suffix = Path(path).suffix.lower()
    if suffix == ".epub":
        return EpubIngestor(path, doc_id=doc_id)
    if suffix == ".pdf":
        return PdfIngestor(path, doc_id=doc_id)
    raise ValueError(f"不支持的输入格式：{suffix}（目前支持 .epub / .pdf）")


__all__ = [
    "EpubError",
    "EpubIngestor",
    "PdfError",
    "PdfIngestor",
    "detect_vertical",
    "ingestor_for",
    "segment_kind",
    "segments_to_paragraphs",
]
