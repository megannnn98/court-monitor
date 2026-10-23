"""Which extraction runs person resolution (ER v2) still has to decide, from PostgreSQL."""

from __future__ import annotations

from sqlalchemy import exists, not_, select
from sqlalchemy.orm import Session, aliased, sessionmaker

from db.arrays import any_of
from ingestion import read
from persons.resolution.service import RESOLVER_VERSION
from persons.storage.models import PersonResolutionDecisionRecord
from processing.extraction.models import ExtractionRunStatus
from processing.extraction.pipeline import ExtractionVersions
from processing.storage.models import ArticleExtractionRunRecord, EntityMentionRecord


class SqlAlchemyPendingResolution:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def extraction_runs_pending_resolution(
        self, *, source_base_url: str, versions: ExtractionVersions
    ) -> list[int]:
        """Succeeded runs of the source with an unlinked person mention that has no ER decision,
        except re-extractions of text an earlier run already covered."""
        decided = exists().where(
            PersonResolutionDecisionRecord.mention_id == EntityMentionRecord.id,
            PersonResolutionDecisionRecord.resolver_version == RESOLVER_VERSION,
        )
        earlier = aliased(ArticleExtractionRunRecord)
        # A re-extraction of unchanged text is resolved on purpose (resolve_runs), not by monitoring.
        reextraction = exists().where(
            earlier.article_id == ArticleExtractionRunRecord.article_id,
            earlier.article_content_hash == ArticleExtractionRunRecord.article_content_hash,
            earlier.status == ExtractionRunStatus.SUCCEEDED.value,
            earlier.id < ArticleExtractionRunRecord.id,
        )
        query = (
            select(ArticleExtractionRunRecord.id)
            .join(
                EntityMentionRecord,
                EntityMentionRecord.extraction_run_id == ArticleExtractionRunRecord.id,
            )
            .where(
                ArticleExtractionRunRecord.status == ExtractionRunStatus.SUCCEEDED.value,
                ArticleExtractionRunRecord.extractor_name == versions.extractor_name,
                ArticleExtractionRunRecord.extractor_version == versions.extractor_version,
                ArticleExtractionRunRecord.normalizer_version == versions.normalizer_version,
                EntityMentionRecord.entity_type == "person",
                EntityMentionRecord.person_id.is_(None),
                not_(decided),
                not_(reextraction),
            )
            .distinct()
            .order_by(ArticleExtractionRunRecord.id)
        )
        with self._session_factory() as session:
            articles = read.article_ids(session, source_base_url=source_base_url)
            if not articles:
                return []
            return list(
                session.scalars(
                    query.where(any_of(ArticleExtractionRunRecord.article_id, articles))
                ).all()
            )
