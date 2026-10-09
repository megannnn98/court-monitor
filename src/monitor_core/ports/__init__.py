"""What the ingestion pipeline needs from a source and a store; the application
supplies the implementations."""

from monitor_core.ports.discovery import SourceAdapter
from monitor_core.ports.fetcher import DocumentFetcher
from monitor_core.ports.parser import ArticleParser
from monitor_core.ports.persistence import IngestionPersistence

__all__ = [
    "ArticleParser",
    "DocumentFetcher",
    "IngestionPersistence",
    "SourceAdapter",
]
