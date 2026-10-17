"""Which stored articles extraction still has to read, decided from PostgreSQL state.

No caller trusts ids handed over in memory: after a crash the next run selects exactly
the work that is still missing.
"""

from __future__ import annotations

from sqlalchemy import exists, not_, select
from sqlalchemy.orm import Session, sessionmaker

from ingestion.storage.models import ParsedArticleRecord, Source, SourceDocument
from processing.extraction.pipeline import ExtractionVersions
from processing.storage.models import ArticleExtractionRunRecord


class SqlAlchemyPendingExtraction:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def articles_pending_extraction(
        self, *, source_base_url: str, versions: ExtractionVersions
    ) -> list[int]:
        """Articles of the source without any extraction run for these versions.

        A FAILED run counts as processed: its failure is deterministic for this
        content and extractor version, so it is not retried every run.
        """
        run_exists = exists().where(
            ArticleExtractionRunRecord.article_id == ParsedArticleRecord.id,
            ArticleExtractionRunRecord.extractor_name == versions.extractor_name,
            ArticleExtractionRunRecord.extractor_version == versions.extractor_version,
            ArticleExtractionRunRecord.normalizer_version == versions.normalizer_version,
        )
        query = (
            select(ParsedArticleRecord.id)
            .join(SourceDocument, ParsedArticleRecord.document_id == SourceDocument.id)
            .join(Source, SourceDocument.source_id == Source.id)
            .where(Source.base_url == source_base_url, not_(run_exists))
            .order_by(ParsedArticleRecord.id)
        )
        with self._session_factory() as session:
            return list(session.scalars(query).all())
