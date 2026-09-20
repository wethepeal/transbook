"""术语抽取与译文 QA。"""

from transbook.quality.compare import CompareReport, EngineResult, compare, run_engine
from transbook.quality.qa import QaIssue, QaReport, check
from transbook.quality.terms import (
    TermCandidate,
    extract_candidates,
    load_glossary,
    write_candidates,
)

__all__ = [
    "CompareReport",
    "EngineResult",
    "QaIssue",
    "QaReport",
    "TermCandidate",
    "check",
    "compare",
    "extract_candidates",
    "load_glossary",
    "run_engine",
    "write_candidates",
]
