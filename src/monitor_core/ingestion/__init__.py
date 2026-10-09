"""Discover, fetch, parse and persist: the orchestration over `monitor_core.ports`."""

from monitor_core.ingestion.pipeline import FetchedArticle, IngestionPipeline
from monitor_core.ingestion.retry import RetryingDocumentFetcher
from monitor_core.ingestion.source_ingestion import (
    ArticleIngestionPipeline,
    SourceIngestion,
    SourceIngestionFailure,
    SourceIngestionResult,
)

__all__ = [
    "ArticleIngestionPipeline",
    "FetchedArticle",
    "IngestionPipeline",
    "RetryingDocumentFetcher",
    "SourceIngestion",
    "SourceIngestionFailure",
    "SourceIngestionResult",
]
