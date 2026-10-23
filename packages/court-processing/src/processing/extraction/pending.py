"""Which stored articles extraction still has to read, decided from PostgreSQL state.

No caller trusts ids handed over in memory: after a crash the next run selects exactly
the work that is still missing.
"""

from __future__ import annotations

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session, sessionmaker

from db.arrays import any_of
from ingestion import read
from processing.extraction.models import ExtractionRunStatus
from processing.extraction.pipeline import ExtractionVersions
from processing.storage.models import ArticleExtractionRunRecord


class SqlAlchemyPendingExtraction:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def articles_pending_extraction(
        self, *, source_base_url: str, versions: ExtractionVersions
    ) -> list[int]:
        """Articles of the source this extractor has not handled yet.

        Handled: a succeeded run of any version, or a run of the current versions. A succeeded
        run of an older version is not pending: a version bump must not queue the whole stored
        corpus behind a monitoring run. Re-extracting it is a separate, explicit operation. A
        FAILED run of the current versions counts as processed: its failure is deterministic
        for this content and extractor version, so it is not retried every run. A FAILED run of
        an older version is retried: the new version may have fixed the cause.
        """
        with self._session_factory() as session:
            articles = read.article_ids(session, source_base_url=source_base_url)
            if not articles:
                return []
            done = set(
                session.scalars(
                    select(ArticleExtractionRunRecord.article_id).where(
                        any_of(ArticleExtractionRunRecord.article_id, articles),
                        ArticleExtractionRunRecord.extractor_name == versions.extractor_name,
                        or_(
                            ArticleExtractionRunRecord.status
                            == ExtractionRunStatus.SUCCEEDED.value,
                            and_(
                                ArticleExtractionRunRecord.extractor_version
                                == versions.extractor_version,
                                ArticleExtractionRunRecord.normalizer_version
                                == versions.normalizer_version,
                            ),
                        ),
                    )
                )
            )
        return [article_id for article_id in articles if article_id not in done]
