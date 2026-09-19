"""输入适配层：把 EPUB / PDF 抽取成统一的 DocumentIR。"""

from transbook.ingest.epub import EpubError, EpubIngestor

__all__ = ["EpubError", "EpubIngestor"]
