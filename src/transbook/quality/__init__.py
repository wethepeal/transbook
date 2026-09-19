"""术语抽取与译文 QA。"""

from transbook.quality.qa import QaIssue, QaReport, check
from transbook.quality.terms import (
    TermCandidate,
    extract_candidates,
    load_glossary,
    write_candidates,
)

__all__ = [
    "QaIssue",
    "QaReport",
    "TermCandidate",
    "check",
    "extract_candidates",
    "load_glossary",
    "write_candidates",
]
