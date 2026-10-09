"""Data handed between the ingestion stages."""

from monitor_core.model.document import (
    IngestionResult,
    ParsedArticle,
    RawDocument,
)
from monitor_core.model.source import SourceReference

__all__ = [
    "IngestionResult",
    "ParsedArticle",
    "RawDocument",
    "SourceReference",
]
