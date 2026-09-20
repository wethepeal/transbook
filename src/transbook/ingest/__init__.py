"""输入适配层：把 EPUB / PDF 抽取成统一的 DocumentIR。"""

from transbook.ingest.epub import EpubError, EpubIngestor
from transbook.ingest.pdf import (
    PdfError,
    PdfIngestor,
    confirm_running_heads,
    detect_vertical,
    find_margin_segments,
    find_page_number_rows,
    is_page_number,
    segment_kind,
    segments_to_paragraphs,
)


def ingestor_for(path, doc_id: str | None = None, *, filter_headers: bool = True,
                 strip_ruby: bool = True):
    """按扩展名选择抽取器（EPUB 优先——结构由作者标注，质量天然高于 PDF 版面推断）。

    `filter_headers` / `strip_ruby` 只对 PDF 有意义（EPUB 的注音是 `<rt>`，走结构剥离），
    故显式声明而不是走 `**kwargs`：走 `**kwargs` 时 `inspect.signature` 查不到这些参数，
    CLI 的开关会被静默忽略（踩过）。
    """
    from pathlib import Path

    suffix = Path(path).suffix.lower()
    if suffix == ".epub":
        return EpubIngestor(path, doc_id=doc_id)
    if suffix == ".pdf":
        return PdfIngestor(path, doc_id=doc_id, filter_headers=filter_headers,
                           strip_ruby=strip_ruby)
    raise ValueError(f"不支持的输入格式：{suffix}（目前支持 .epub / .pdf）")


__all__ = [
    "EpubError",
    "EpubIngestor",
    "PdfError",
    "PdfIngestor",
    "confirm_running_heads",
    "detect_vertical",
    "find_margin_segments",
    "find_page_number_rows",
    "ingestor_for",
    "is_page_number",
    "segment_kind",
    "segments_to_paragraphs",
]
