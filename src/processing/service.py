"""The processing module's public API, up to and including extraction.

One article per call, so the caller keeps its own loop. The outcome is persisted: an
extraction run with its mentions and events. Nothing is returned for the next stage to
carry; it selects its work from the database.
"""

from __future__ import annotations

from typing import Protocol

from processing.extraction.documents import SqlAlchemyExtractionDocumentRepository
from processing.extraction.models import ExtractionSaveResult
from processing.extraction.pipeline import ExtractionPipeline, ExtractionVersions


class PendingExtraction(Protocol):
    """Which stored articles of a source have no extraction run for these versions."""

    def articles_pending_extraction(
        self, *, source_base_url: str, versions: ExtractionVersions
    ) -> list[int]: ...


class ProcessingService:
    def __init__(
        self,
        *,
        pipeline: ExtractionPipeline,
        documents: SqlAlchemyExtractionDocumentRepository,
        pending: PendingExtraction,
    ) -> None:
        self._pipeline = pipeline
        self._documents = documents
        self._pending = pending

    def articles_pending_extraction(self, *, source_base_url: str) -> list[int]:
        return self._pending.articles_pending_extraction(
            source_base_url=source_base_url, versions=self._pipeline.versions
        )

    def extract_article(self, article_id: int) -> ExtractionSaveResult:
        """A FAILED status is an outcome of the article, stored with its run; an
        exception is a failure of the call."""
        return self._pipeline.run(self._documents.get_by_article_id(article_id))
