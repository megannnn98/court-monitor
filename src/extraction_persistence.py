from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from extraction_models import (
    ArticleExtractionResult,
    ExtractionDocument,
    ExtractionRunStatus,
    ExtractionSaveResult,
)
from orm_models import (
    ArticleExtractionRunRecord,
    EntityMentionRecord,
    EventEntityMentionRecord,
    ExtractedEventRecord,
)


class SqlAlchemyExtractionPersistence:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def save(self, result: ArticleExtractionResult) -> ExtractionSaveResult:
        with self._session_factory.begin() as session:
            existing = self._find_run(
                session,
                document=result.document,
                extractor_name=result.extractor_name,
                extractor_version=result.extractor_version,
                normalizer_version=result.normalizer_version,
            )
            if existing is not None and existing.status == ExtractionRunStatus.SUCCEEDED.value:
                return ExtractionSaveResult(
                    run_id=existing.id,
                    article_id=result.document.article_id,
                    status=ExtractionRunStatus.SUCCEEDED,
                    skipped_existing=True,
                )

            run = existing or ArticleExtractionRunRecord(
                article_id=result.document.article_id,
                article_content_hash=result.document.content_hash,
                extractor_name=result.extractor_name,
                extractor_version=result.extractor_version,
                normalizer_version=result.normalizer_version,
                status=ExtractionRunStatus.RUNNING.value,
                started_at=datetime.now(UTC),
            )
            run.status = ExtractionRunStatus.RUNNING.value
            run.error_message = None
            session.add(run)
            session.flush()

            self._delete_children(session, run.id)
            mention_records = [
                EntityMentionRecord(
                    extraction_run_id=run.id,
                    entity_type=mention.entity_type.value,
                    surface_text=mention.surface_text,
                    normalized_text=mention.normalized_text,
                    start_offset=mention.start_offset,
                    end_offset=mention.end_offset,
                    confidence=mention.confidence,
                    normalized_data=mention.normalized_data.model_dump(mode="json"),
                    extractor_name=mention.extractor_name,
                    extractor_version=mention.extractor_version,
                    normalizer_version=mention.normalizer_version,
                )
                for mention in result.mentions
            ]
            session.add_all(mention_records)
            session.flush()

            event_records: list[ExtractedEventRecord] = []
            for event in result.events:
                event_record = ExtractedEventRecord(
                    extraction_run_id=run.id,
                    event_type=event.event_type.value,
                    event_date=event.event_date,
                    start_offset=event.start_offset,
                    end_offset=event.end_offset,
                    confidence=event.confidence,
                    attributes=event.attributes,
                    extractor_name=event.extractor_name,
                    extractor_version=event.extractor_version,
                )
                session.add(event_record)
                session.flush()
                for link in event.links:
                    session.add(
                        EventEntityMentionRecord(
                            event_id=event_record.id,
                            mention_id=mention_records[link.mention_index].id,
                            role=link.role.value,
                        )
                    )
                event_records.append(event_record)

            run.status = ExtractionRunStatus.SUCCEEDED.value
            run.finished_at = datetime.now(UTC)

            return ExtractionSaveResult(
                run_id=run.id,
                article_id=result.document.article_id,
                status=ExtractionRunStatus.SUCCEEDED,
                mentions_created=len(mention_records),
                events_created=len(event_records),
            )

    def save_failed(
        self,
        document: ExtractionDocument,
        *,
        extractor_name: str,
        extractor_version: str,
        normalizer_version: str,
        error_message: str,
    ) -> ExtractionSaveResult:
        with self._session_factory.begin() as session:
            run = self._find_run(
                session,
                document=document,
                extractor_name=extractor_name,
                extractor_version=extractor_version,
                normalizer_version=normalizer_version,
            )
            if run is not None and run.status == ExtractionRunStatus.SUCCEEDED.value:
                return ExtractionSaveResult(
                    run_id=run.id,
                    article_id=document.article_id,
                    status=ExtractionRunStatus.SUCCEEDED,
                    skipped_existing=True,
                )
            if run is None:
                run = ArticleExtractionRunRecord(
                    article_id=document.article_id,
                    article_content_hash=document.content_hash,
                    extractor_name=extractor_name,
                    extractor_version=extractor_version,
                    normalizer_version=normalizer_version,
                    status=ExtractionRunStatus.FAILED.value,
                    started_at=datetime.now(UTC),
                )
                session.add(run)
                session.flush()
            self._delete_children(session, run.id)
            run.status = ExtractionRunStatus.FAILED.value
            run.finished_at = datetime.now(UTC)
            run.error_message = error_message[:2000]
            return ExtractionSaveResult(
                run_id=run.id,
                article_id=document.article_id,
                status=ExtractionRunStatus.FAILED,
                error_message=run.error_message,
            )

    @staticmethod
    def _find_run(
        session: Session,
        *,
        document: ExtractionDocument,
        extractor_name: str,
        extractor_version: str,
        normalizer_version: str,
    ) -> ArticleExtractionRunRecord | None:
        return session.scalar(
            select(ArticleExtractionRunRecord).where(
                ArticleExtractionRunRecord.article_id == document.article_id,
                ArticleExtractionRunRecord.article_content_hash == document.content_hash,
                ArticleExtractionRunRecord.extractor_name == extractor_name,
                ArticleExtractionRunRecord.extractor_version == extractor_version,
                ArticleExtractionRunRecord.normalizer_version == normalizer_version,
            )
        )

    @staticmethod
    def _delete_children(session: Session, run_id: int) -> None:
        event_ids = session.scalars(
            select(ExtractedEventRecord.id).where(ExtractedEventRecord.extraction_run_id == run_id)
        ).all()
        if event_ids:
            session.execute(
                delete(EventEntityMentionRecord).where(
                    EventEntityMentionRecord.event_id.in_(event_ids)
                )
            )
        session.execute(
            delete(ExtractedEventRecord).where(ExtractedEventRecord.extraction_run_id == run_id)
        )
        session.execute(
            delete(EntityMentionRecord).where(EntityMentionRecord.extraction_run_id == run_id)
        )

    def get_latest_run_by_article_id(self, article_id: int) -> int | None:
        """Get the latest extraction run ID for a given article ID."""
        with self._session_factory() as session:
            run = session.scalar(
                select(ArticleExtractionRunRecord)
                .where(
                    ArticleExtractionRunRecord.article_id == article_id,
                    ArticleExtractionRunRecord.status == ExtractionRunStatus.SUCCEEDED.value,
                )
                .order_by(ArticleExtractionRunRecord.finished_at.desc())
                .limit(1)
            )
            return run.id if run else None
