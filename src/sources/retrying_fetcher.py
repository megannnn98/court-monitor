"""The retrying fetcher lives in `monitor_core.ingestion`; re-exported for existing imports."""

from monitor_core.ingestion.retry import RetryingDocumentFetcher

__all__ = ["RetryingDocumentFetcher"]
