"""Which rows a monitoring stage still has to process, decided from PostgreSQL state.

No stage trusts the IDs a previous stage handed over in memory: after a crash
(or any at-least-once re-execution) the next run selects exactly the work that
is still missing, and finished work is not selected again.

A person's derived results (classification, Rosfinmonitoring match, semantic
document) are stale when that person's evidence changed after the result was
written. Evidence change is the newest of: person created/updated, event
linked, alias added, ER decision recorded or reviewed for the person.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy import Select, Subquery, and_, exists, func, not_, or_, select, union, union_all
from sqlalchemy.orm import Session, sessionmaker

from db.orm_models import (
    ArticleExtractionRunRecord,
    EntityMentionRecord,
    ExtractedEventRecord,
    ParsedArticleRecord,
    PersecutionClassificationRecord,
    PersonAliasRecord,
    PersonEventLinkRecord,
    PersonRecord,
    PersonResolutionDecisionRecord,
    RosfinMatchRecord,
    SemanticDocumentRecord,
    Source,
    SourceDocument,
)
from extraction.models import ExtractionRunStatus
from extraction.pipeline import ExtractionVersions
from persons.models import PersonStatus
from persons.resolution.service import RESOLVER_VERSION
from semantic_retrieval.models import RetrievalEntityType


def _person_change_stamps(*, include_derived: bool) -> Subquery:
    """(person_id, changed_at): newest evidence change per person."""
    decision = PersonResolutionDecisionRecord
    parts: list[Select[Any]] = [
        select(PersonRecord.id.label("person_id"), PersonRecord.created_at.label("changed_at")),
        select(PersonRecord.id, PersonRecord.updated_at).where(
            PersonRecord.updated_at.is_not(None)
        ),
        select(PersonEventLinkRecord.person_id, PersonEventLinkRecord.created_at),
        select(PersonAliasRecord.person_id, PersonAliasRecord.created_at),
        select(decision.selected_person_id, decision.created_at).where(
            decision.selected_person_id.is_not(None)
        ),
        select(decision.selected_person_id, decision.reviewed_at).where(
            decision.selected_person_id.is_not(None), decision.reviewed_at.is_not(None)
        ),
    ]
    if include_derived:
        # The person semantic document also renders classification and RF data.
        parts.append(
            select(
                PersecutionClassificationRecord.person_id,
                PersecutionClassificationRecord.classified_at,
            )
        )
        parts.append(select(RosfinMatchRecord.person_id, RosfinMatchRecord.matched_at))
    stamps = union_all(*parts).subquery()
    return (
        select(stamps.c.person_id, func.max(stamps.c.changed_at).label("changed_at"))
        .group_by(stamps.c.person_id)
        .subquery()
    )


class SqlAlchemyMonitoringWorkQueries:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def known_external_ids(self, *, source_base_url: str, external_ids: Sequence[str]) -> set[str]:
        if not external_ids:
            return set()
        query = (
            select(SourceDocument.external_id)
            .join(Source, SourceDocument.source_id == Source.id)
            .where(
                Source.base_url == source_base_url,
                SourceDocument.external_id.in_(list(external_ids)),
            )
        )
        with self._session_factory() as session:
            return set(session.scalars(query).all())

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

    def extraction_runs_pending_resolution(
        self, *, source_base_url: str, versions: ExtractionVersions
    ) -> list[int]:
        """Succeeded runs of the source with an unlinked person mention that has no ER decision."""
        decided = exists().where(
            PersonResolutionDecisionRecord.mention_id == EntityMentionRecord.id,
            PersonResolutionDecisionRecord.resolver_version == RESOLVER_VERSION,
        )
        query = (
            select(ArticleExtractionRunRecord.id)
            .join(
                ParsedArticleRecord, ArticleExtractionRunRecord.article_id == ParsedArticleRecord.id
            )
            .join(SourceDocument, ParsedArticleRecord.document_id == SourceDocument.id)
            .join(Source, SourceDocument.source_id == Source.id)
            .join(
                EntityMentionRecord,
                EntityMentionRecord.extraction_run_id == ArticleExtractionRunRecord.id,
            )
            .where(
                Source.base_url == source_base_url,
                ArticleExtractionRunRecord.status == ExtractionRunStatus.SUCCEEDED.value,
                ArticleExtractionRunRecord.extractor_name == versions.extractor_name,
                ArticleExtractionRunRecord.extractor_version == versions.extractor_version,
                ArticleExtractionRunRecord.normalizer_version == versions.normalizer_version,
                EntityMentionRecord.entity_type == "person",
                EntityMentionRecord.person_id.is_(None),
                not_(decided),
            )
            .distinct()
            .order_by(ArticleExtractionRunRecord.id)
        )
        with self._session_factory() as session:
            return list(session.scalars(query).all())

    def persons_pending_classification(
        self, *, classifier_name: str, classifier_version: str
    ) -> list[int]:
        changed = _person_change_stamps(include_derived=False)
        classification = PersecutionClassificationRecord
        query = (
            select(PersonRecord.id)
            .join(changed, changed.c.person_id == PersonRecord.id)
            .outerjoin(
                classification,
                and_(
                    classification.person_id == PersonRecord.id,
                    classification.classifier_name == classifier_name,
                    classification.classifier_version == classifier_version,
                ),
            )
            .where(
                PersonRecord.status == PersonStatus.ACTIVE.value,
                or_(
                    classification.id.is_(None),
                    classification.classified_at < changed.c.changed_at,
                ),
            )
            .order_by(PersonRecord.id)
        )
        with self._session_factory() as session:
            return list(session.scalars(query).all())

    def persons_pending_rf_match(self, *, snapshot_id: int) -> list[int]:
        changed = _person_change_stamps(include_derived=False)
        query = (
            select(PersonRecord.id)
            .join(changed, changed.c.person_id == PersonRecord.id)
            .outerjoin(
                RosfinMatchRecord,
                and_(
                    RosfinMatchRecord.person_id == PersonRecord.id,
                    RosfinMatchRecord.snapshot_id == snapshot_id,
                ),
            )
            .where(
                PersonRecord.status == PersonStatus.ACTIVE.value,
                or_(
                    RosfinMatchRecord.id.is_(None),
                    RosfinMatchRecord.matched_at < changed.c.changed_at,
                ),
            )
            .order_by(PersonRecord.id)
        )
        with self._session_factory() as session:
            return list(session.scalars(query).all())

    def persons_pending_semantic_index(self) -> list[int]:
        """Active persons with a missing/unindexed/outdated document, and documents of
        persons that are gone or no longer active (the indexer deletes those)."""
        changed = _person_change_stamps(include_derived=True)
        document = SemanticDocumentRecord
        is_person_document = and_(
            document.entity_type == RetrievalEntityType.PERSON.value,
            document.entity_id == PersonRecord.id,
        )
        stale_active = (
            select(PersonRecord.id)
            .join(changed, changed.c.person_id == PersonRecord.id)
            .outerjoin(document, is_person_document)
            .where(
                PersonRecord.status == PersonStatus.ACTIVE.value,
                or_(
                    document.id.is_(None),
                    document.indexed_at.is_(None),
                    document.updated_at < changed.c.changed_at,
                ),
            )
        )
        inactive_indexed = (
            select(PersonRecord.id)
            .join(document, is_person_document)
            .where(PersonRecord.status != PersonStatus.ACTIVE.value)
        )
        orphaned = select(document.entity_id).where(
            document.entity_type == RetrievalEntityType.PERSON.value,
            not_(exists().where(PersonRecord.id == document.entity_id)),
        )
        combined = union(stale_active, inactive_indexed, orphaned).subquery()
        with self._session_factory() as session:
            return sorted(session.scalars(select(combined.c[0])).all())

    def events_pending_semantic_index(self) -> list[int]:
        links = (
            select(
                PersonEventLinkRecord.event_id,
                func.max(PersonEventLinkRecord.created_at).label("changed_at"),
            )
            .group_by(PersonEventLinkRecord.event_id)
            .subquery()
        )
        document = SemanticDocumentRecord
        stale = (
            select(ExtractedEventRecord.id)
            .outerjoin(
                document,
                and_(
                    document.entity_type == RetrievalEntityType.EVENT.value,
                    document.entity_id == ExtractedEventRecord.id,
                ),
            )
            .outerjoin(links, links.c.event_id == ExtractedEventRecord.id)
            .where(
                or_(
                    document.id.is_(None),
                    document.indexed_at.is_(None),
                    and_(
                        links.c.changed_at.is_not(None),
                        document.updated_at < links.c.changed_at,
                    ),
                )
            )
        )
        orphaned = select(document.entity_id).where(
            document.entity_type == RetrievalEntityType.EVENT.value,
            not_(exists().where(ExtractedEventRecord.id == document.entity_id)),
        )
        combined = union(stale, orphaned).subquery()
        with self._session_factory() as session:
            return sorted(session.scalars(select(combined.c[0])).all())
