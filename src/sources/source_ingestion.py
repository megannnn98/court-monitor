"""Source ingestion lives in `monitor_core.ingestion`; re-exported for existing imports."""

from monitor_core.ingestion.source_ingestion import (
    ArticleIngestionPipeline,
    SourceIngestion,
    SourceIngestionFailure,
    SourceIngestionResult,
)

__all__ = [
    "ArticleIngestionPipeline",
    "SourceIngestion",
    "SourceIngestionFailure",
    "SourceIngestionResult",
]
