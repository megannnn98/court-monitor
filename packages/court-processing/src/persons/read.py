"""Read contracts for the retained ER v2 review station.

Queries run in the caller's transaction; immutable values cross the package boundary.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from db.arrays import any_of
from ingestion.read import stored_articles
from persons.storage.models import (
    PersonAliasRecord,
    PersonEventLinkRecord,
    PersonRecord,
    ReviewRecordModel,
)
from processing.storage.models import ArticleExtractionRunRecord, ExtractedEventRecord


@dataclass(frozen=True)
class PersonSummary:
    id: int
    canonical_name: str
    normalized_name: str
    matching_key: str
    status: str
    merged_into_id: int | None


def _person(row: PersonRecord) -> PersonSummary:
    return PersonSummary(
        row.id,
        row.canonical_name,
        row.normalized_name,
        row.matching_key,
        row.status,
        row.merged_into_id,
    )


def person(session: Session, person_id: int) -> PersonSummary | None:
    row = session.get(PersonRecord, person_id)
    return None if row is None else _person(row)


def unmerged_person_ids(session: Session, *, limit: int | None = None) -> list[int]:
    """Canonical identities eligible for a snapshot check, in stable id order."""
    query = (
        select(PersonRecord.id)
        .where(PersonRecord.merged_into_id.is_(None))
        .order_by(PersonRecord.id)
    )
    if limit is not None:
        query = query.limit(limit)
    return list(session.scalars(query).all())


def persons(session: Session, ids: Collection[int]) -> dict[int, PersonSummary]:
    if not ids:
        return {}
    return {
        row.id: _person(row)
        for row in session.scalars(select(PersonRecord).where(any_of(PersonRecord.id, ids)))
    }


def browse_persons(
    session: Session, *, status: str | None, limit: int, offset: int
) -> list[PersonSummary]:
    query = select(PersonRecord).order_by(PersonRecord.id).offset(offset).limit(limit)
    if status:
        query = query.where(PersonRecord.status == status)
    return [_person(row) for row in session.scalars(query)]


@dataclass(frozen=True)
class Alias:
    id: int
    person_id: int
    surface_text: str
    normalized_text: str
    matching_key: str
    origin: str
    confidence: float


def aliases(session: Session, person_id: int, *, ordered: bool = False) -> list[Alias]:
    """Aliases of a person; the detail card orders them, the legacy endpoint does not."""
    query = select(PersonAliasRecord).where(PersonAliasRecord.person_id == person_id)
    if ordered:
        query = query.order_by(PersonAliasRecord.id)
    return [
        Alias(
            row.id,
            row.person_id,
            row.surface_text,
            row.normalized_text,
            row.matching_key,
            row.origin,
            row.confidence,
        )
        for row in session.scalars(query)
    ]


@dataclass(frozen=True)
class EventEvidence:
    id: int
    event_type: str
    event_date: datetime | None
    role: str
    confidence: float
    article_id: int
    source_name: str
    title: str
    url: str
    start_offset: int
    end_offset: int
    text: str


def event_evidence(session: Session, person_id: int) -> list[EventEvidence]:
    """The person's events and source spans, using bulk reads for publication evidence."""
    rows = session.execute(
        select(
            ExtractedEventRecord.id,
            ExtractedEventRecord.event_type,
            ExtractedEventRecord.event_date,
            PersonEventLinkRecord.role,
            PersonEventLinkRecord.confidence,
            ArticleExtractionRunRecord.article_id,
            ExtractedEventRecord.start_offset,
            ExtractedEventRecord.end_offset,
        )
        .select_from(PersonEventLinkRecord)
        .join(ExtractedEventRecord, ExtractedEventRecord.id == PersonEventLinkRecord.event_id)
        .join(
            ArticleExtractionRunRecord,
            ArticleExtractionRunRecord.id == ExtractedEventRecord.extraction_run_id,
        )
        .where(PersonEventLinkRecord.person_id == person_id)
        .order_by(
            ExtractedEventRecord.event_date.desc().nullslast(), ExtractedEventRecord.id.desc()
        )
    ).all()
    articles = stored_articles(session, {row.article_id for row in rows})
    return [
        EventEvidence(
            id=row.id,
            event_type=row.event_type,
            event_date=row.event_date,
            role=row.role,
            confidence=row.confidence,
            article_id=row.article_id,
            source_name=articles[row.article_id].source_name,
            title=articles[row.article_id].title,
            url=articles[row.article_id].url,
            start_offset=row.start_offset,
            end_offset=row.end_offset,
            # Preserve Python slicing even for unusual persisted offsets.
            text=articles[row.article_id].text[row.start_offset : row.end_offset],
        )
        for row in rows
        if row.article_id in articles
    ]


@dataclass(frozen=True)
class ReviewSummary:
    id: int
    subject_type: str
    subject_id: int
    decision: str | None
    confidence: float | None
    reason: str | None
    reviewer_note: str | None
    created_at: datetime
    reviewed_at: datetime | None


def reviews(
    session: Session,
    *,
    status: str | None,
    subject_type: str | None,
    limit: int,
    offset: int,
) -> list[ReviewSummary]:
    """Review history, newest first; filters retain the legacy empty-string semantics."""
    query = select(ReviewRecordModel).offset(offset).limit(limit)
    if status:
        query = query.where(ReviewRecordModel.decision == status)
    if subject_type:
        query = query.where(ReviewRecordModel.subject_type == subject_type)
    query = query.order_by(ReviewRecordModel.created_at.desc(), ReviewRecordModel.id.desc())
    return [
        ReviewSummary(
            id=r.id,
            subject_type=r.subject_type,
            subject_id=r.subject_id,
            decision=r.decision,
            confidence=r.confidence,
            reason=r.reason,
            reviewer_note=r.reviewer_note,
            created_at=r.created_at,
            reviewed_at=r.reviewed_at,
        )
        for r in session.scalars(query)
    ]


@dataclass(frozen=True)
class PersonStats:
    event_count: int
    last_event_date: datetime | None
    alias_count: int


def person_stats(session: Session, ids: Collection[int]) -> dict[int, PersonStats]:
    """Per person asked for: its linked events, the latest event date and its aliases."""
    if not ids:
        return {}
    events = {
        person_id: (count, last)
        for person_id, count, last in session.execute(
            select(
                PersonEventLinkRecord.person_id,
                func.count(PersonEventLinkRecord.id),
                func.max(ExtractedEventRecord.event_date),
            )
            .join(ExtractedEventRecord, PersonEventLinkRecord.event_id == ExtractedEventRecord.id)
            .where(any_of(PersonEventLinkRecord.person_id, ids))
            .group_by(PersonEventLinkRecord.person_id)
        ).all()
    }
    aliases = dict(
        session.execute(
            select(PersonAliasRecord.person_id, func.count(PersonAliasRecord.id))
            .where(any_of(PersonAliasRecord.person_id, ids))
            .group_by(PersonAliasRecord.person_id)
        )
        .tuples()
        .all()
    )
    return {
        person_id: PersonStats(
            events.get(person_id, (0, None))[0],
            events.get(person_id, (0, None))[1],
            aliases.get(person_id, 0),
        )
        for person_id in ids
    }
