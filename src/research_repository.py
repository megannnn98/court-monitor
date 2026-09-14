"""PostgreSQL read model for person research."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, datetime, time, timedelta

from sqlalchemy import ColumnElement, exists, func, or_, select
from sqlalchemy.orm import InstrumentedAttribute, Session, sessionmaker

from extraction_models import EventEntityRole
from orm_models import (
    ArticleExtractionRunRecord,
    EntityMentionRecord,
    ExtractedEventRecord,
    ParsedArticleRecord,
    PersecutionClassificationRecord,
    PersonAliasRecord,
    PersonEventLinkRecord,
    PersonRecord,
    RosfinMatchRecord,
    RosfinmonitoringSnapshotRecord,
    Source,
    SourceDocument,
)
from persecution_models import PersecutionClassification
from person_models import PersonStatus
from research_mapping import (
    alias_from_record,
    classification_from_record,
    event_evidence,
    mention_evidence,
    person_from_record,
    research_event,
    rosfinmonitoring_from_match,
)
from research_models import PersonResearchCriteria, ResearchRosfinmonitoring, ResearchSource
from research_service import PersonResearchDetails


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class SqlAlchemyPersonResearchRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def snapshot_exists(self, snapshot_id: int) -> bool:
        with self._session_factory() as session:
            return session.get(RosfinmonitoringSnapshotRecord, snapshot_id) is not None

    def find_person_ids(self, criteria: PersonResearchCriteria) -> list[int]:
        # Same population as the candidate query: active canonical persons only.
        query = select(PersonRecord.id).where(PersonRecord.status == PersonStatus.ACTIVE.value)

        if criteria.person_id is not None:
            query = query.where(PersonRecord.id == criteria.person_id)

        if criteria.name is not None:
            pattern = f"%{_escape_like(criteria.name)}%"
            query = query.where(
                or_(
                    PersonRecord.canonical_name.ilike(pattern, escape="\\"),
                    PersonRecord.normalized_name.ilike(pattern, escape="\\"),
                    exists().where(
                        PersonAliasRecord.person_id == PersonRecord.id,
                        or_(
                            PersonAliasRecord.surface_text.ilike(pattern, escape="\\"),
                            PersonAliasRecord.normalized_text.ilike(pattern, escape="\\"),
                        ),
                    ),
                )
            )

        event_conditions = self._event_conditions(criteria)
        if event_conditions:
            query = query.where(
                exists().where(
                    PersonEventLinkRecord.person_id == PersonRecord.id,
                    PersonEventLinkRecord.event_id == ExtractedEventRecord.id,
                    *event_conditions,
                )
            )

        if criteria.source is not None:
            query = query.where(
                or_(
                    exists().where(
                        EntityMentionRecord.person_id == PersonRecord.id,
                        *self._run_from_source(
                            EntityMentionRecord.extraction_run_id, criteria.source
                        ),
                    ),
                    exists().where(
                        PersonEventLinkRecord.person_id == PersonRecord.id,
                        PersonEventLinkRecord.event_id == ExtractedEventRecord.id,
                        *self._run_from_source(
                            ExtractedEventRecord.extraction_run_id, criteria.source
                        ),
                    ),
                )
            )

        with self._session_factory() as session:
            return list(session.scalars(query.order_by(PersonRecord.id)).all())

    @staticmethod
    def _event_conditions(criteria: PersonResearchCriteria) -> list[ColumnElement[bool]]:
        conditions: list[ColumnElement[bool]] = []
        if criteria.event_types is not None:
            conditions.append(
                ExtractedEventRecord.event_type.in_([t.value for t in criteria.event_types])
            )
        if criteria.date_from is not None:
            conditions.append(
                ExtractedEventRecord.event_date
                >= datetime.combine(criteria.date_from, time.min, UTC)
            )
        if criteria.date_to is not None:
            # Inclusive calendar day: strictly before the next day's midnight.
            conditions.append(
                ExtractedEventRecord.event_date
                < datetime.combine(criteria.date_to + timedelta(days=1), time.min, UTC)
            )
        return conditions

    @staticmethod
    def _run_from_source(
        run_id_column: InstrumentedAttribute[int], source_name: str
    ) -> list[ColumnElement[bool]]:
        return [
            ArticleExtractionRunRecord.id == run_id_column,
            ParsedArticleRecord.id == ArticleExtractionRunRecord.article_id,
            SourceDocument.id == ParsedArticleRecord.document_id,
            Source.id == SourceDocument.source_id,
            Source.name == source_name,
        ]

    def get_latest_classifications(
        self, person_ids: Sequence[int]
    ) -> dict[int, PersecutionClassification]:
        if not person_ids:
            return {}
        with self._session_factory() as session:
            records = session.scalars(
                select(PersecutionClassificationRecord)
                .where(PersecutionClassificationRecord.person_id.in_(person_ids))
                .order_by(
                    PersecutionClassificationRecord.person_id,
                    PersecutionClassificationRecord.classified_at.desc(),
                    PersecutionClassificationRecord.id.desc(),
                )
            ).all()
        latest: dict[int, PersecutionClassification] = {}
        for record in records:
            if record.person_id not in latest:
                latest[record.person_id] = classification_from_record(record)
        return latest

    def get_rosfinmonitoring(
        self, person_ids: Sequence[int], snapshot_id: int
    ) -> dict[int, ResearchRosfinmonitoring]:
        if not person_ids:
            return {}
        with self._session_factory() as session:
            records = session.scalars(
                select(RosfinMatchRecord).where(
                    RosfinMatchRecord.snapshot_id == snapshot_id,
                    RosfinMatchRecord.person_id.in_(person_ids),
                )
            ).all()
        by_person = {record.person_id: record for record in records}
        return {
            person_id: rosfinmonitoring_from_match(
                by_person.get(person_id), snapshot_id=snapshot_id
            )
            for person_id in person_ids
        }

    def get_person_details(self, person_ids: Sequence[int]) -> dict[int, PersonResearchDetails]:
        if not person_ids:
            return {}
        with self._session_factory() as session:
            persons = session.scalars(
                select(PersonRecord).where(PersonRecord.id.in_(person_ids))
            ).all()
            details = {
                person.id: PersonResearchDetails(person=person_from_record(person))
                for person in persons
            }

            for alias in session.scalars(
                select(PersonAliasRecord)
                .where(PersonAliasRecord.person_id.in_(person_ids))
                .order_by(PersonAliasRecord.id)
            ).all():
                details[alias.person_id].aliases.append(alias_from_record(alias))

            article_ids_by_person: dict[int, set[int]] = defaultdict(set)

            # Only mentions resolved to this person — never every mention of
            # the article the person appears in.
            mention_rows = session.execute(
                select(EntityMentionRecord, ArticleExtractionRunRecord.article_id)
                .join(
                    ArticleExtractionRunRecord,
                    ArticleExtractionRunRecord.id == EntityMentionRecord.extraction_run_id,
                )
                .where(EntityMentionRecord.person_id.in_(person_ids))
                .order_by(EntityMentionRecord.id)
            ).all()
            for mention, article_id in mention_rows:
                if mention.person_id is None:  # excluded by the WHERE clause
                    continue
                details[mention.person_id].evidence.append(
                    mention_evidence(mention, article_id=article_id)
                )
                article_ids_by_person[mention.person_id].add(article_id)

            # Only events linked to this person via person_event_links.
            event_rows = session.execute(
                select(
                    PersonEventLinkRecord.person_id,
                    PersonEventLinkRecord.role,
                    ExtractedEventRecord,
                    ArticleExtractionRunRecord.article_id,
                    func.substr(
                        ParsedArticleRecord.text,
                        ExtractedEventRecord.start_offset + 1,
                        ExtractedEventRecord.end_offset - ExtractedEventRecord.start_offset,
                    ),
                )
                .join(
                    ExtractedEventRecord, ExtractedEventRecord.id == PersonEventLinkRecord.event_id
                )
                .join(
                    ArticleExtractionRunRecord,
                    ArticleExtractionRunRecord.id == ExtractedEventRecord.extraction_run_id,
                )
                .join(
                    ParsedArticleRecord,
                    ParsedArticleRecord.id == ArticleExtractionRunRecord.article_id,
                )
                .where(PersonEventLinkRecord.person_id.in_(person_ids))
                .order_by(
                    PersonEventLinkRecord.person_id,
                    ExtractedEventRecord.event_date.nulls_last(),
                    ExtractedEventRecord.id,
                )
            ).all()
            roles: dict[tuple[int, int], list[EventEntityRole]] = defaultdict(list)
            events_by_key: dict[tuple[int, int], tuple[ExtractedEventRecord, int, str]] = {}
            for person_id, role, event, article_id, span_text in event_rows:
                key = (person_id, event.id)
                roles[key].append(EventEntityRole(role))
                events_by_key.setdefault(key, (event, article_id, span_text))
            for (person_id, _), (event, article_id, span_text) in events_by_key.items():
                details[person_id].events.append(
                    research_event(event, roles=roles[(person_id, event.id)], article_id=article_id)
                )
                details[person_id].evidence.append(
                    event_evidence(event, article_id=article_id, span_text=span_text)
                )
                article_ids_by_person[person_id].add(article_id)

            all_article_ids = set().union(*article_ids_by_person.values())
            sources_by_article: dict[int, ResearchSource] = {}
            if all_article_ids:
                for article, url, source_name in session.execute(
                    select(ParsedArticleRecord, SourceDocument.canonical_url, Source.name)
                    .join(SourceDocument, SourceDocument.id == ParsedArticleRecord.document_id)
                    .join(Source, Source.id == SourceDocument.source_id)
                    .where(ParsedArticleRecord.id.in_(all_article_ids))
                ).all():
                    sources_by_article[article.id] = ResearchSource(
                        article_id=article.id,
                        article_title=article.title,
                        source_name=source_name,
                        url=url,
                        published_at=article.published_at,
                    )

        for person_id, article_ids in article_ids_by_person.items():
            details[person_id].sources = [
                sources_by_article[article_id] for article_id in sorted(article_ids)
            ]
        return details
