"""Read contracts for the retained ER v2 review station.

Queries run in the caller's transaction; immutable values cross the package boundary.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import Select, func, or_, select, union_all
from sqlalchemy.orm import Session

from db.arrays import any_of
from ingestion.read import published_days, stored_articles
from persons.models import PersonStatus
from persons.storage.models import (
    PersonAliasRecord,
    PersonEventLinkRecord,
    PersonRecord,
    PersonResolutionDecisionRecord,
    ReviewRecordModel,
)
from processing.storage.models import (
    ArticleExtractionRunRecord,
    EntityMentionRecord,
    ExtractedEventRecord,
)


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
    """The person's events and source spans, using bulk reads for publication evidence.

    Latest first: by the event's own date where the text gave one, otherwise by the date its
    publication came out. The publication date is an ordering key here only — it is never
    written into the event's date.
    """
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
    ).all()
    articles = stored_articles(session, {row.article_id for row in rows})
    days = published_days(session, {row.article_id for row in rows})
    found = [
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
    return sorted(
        found,
        key=lambda event: (
            _is_null(event.event_date),
            -(event.event_date.timestamp() if event.event_date else 0.0),
            _sort_day(days.get(event.article_id)),
            -event.id,
        ),
    )


def _is_null(moment: datetime | None) -> bool:
    """Sorts a date last when it is unknown, the way `NULLS LAST` does in SQL."""
    return moment is None


def _sort_day(day: date | None) -> int:
    """A day as a number that sorts descending; an unknown one sorts last."""
    return -(day.toordinal() if day else 0)


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
    # The latest date the texts gave for this person's events; None when none of them gave
    # one. Never the publication date: this is the date of the event or nothing.
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


def active_evidence_changes(session: Session, *, settle: timedelta) -> dict[int, datetime | None]:
    """Per active person, in id order: the newest change of its evidence (created or
    updated, an event linked, an alias added, an ER decision recorded or reviewed for
    it), plus `settle`."""
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
    stamps = union_all(*parts).subquery()
    changed = (
        select(stamps.c.person_id, (func.max(stamps.c.changed_at) + settle).label("changed_at"))
        .group_by(stamps.c.person_id)
        .subquery()
    )
    rows = session.execute(
        select(PersonRecord.id, changed.c.changed_at)
        .join(changed, changed.c.person_id == PersonRecord.id)
        .where(PersonRecord.status == PersonStatus.ACTIVE.value)
        .order_by(PersonRecord.id)
    ).all()
    return {person_id: at for person_id, at in rows}


# -- a person's evidence, for the ER v2 persecution classifier ---------------------


@dataclass(frozen=True)
class MentionSpan:
    id: int
    extraction_run_id: int
    start_offset: int
    end_offset: int
    surface_text: str
    normalized_text: str


@dataclass(frozen=True)
class EventSpan:
    id: int
    extraction_run_id: int
    event_type: str
    event_date: datetime | None
    confidence: float
    attributes: dict[str, Any]
    start_offset: int
    end_offset: int


_MENTION = (
    EntityMentionRecord.id,
    EntityMentionRecord.extraction_run_id,
    EntityMentionRecord.start_offset,
    EntityMentionRecord.end_offset,
    EntityMentionRecord.surface_text,
    EntityMentionRecord.normalized_text,
)


def linked_person_ids(session: Session) -> list[int]:
    """Every person an event is linked to."""
    return list(session.scalars(select(PersonEventLinkRecord.person_id).distinct()).all())


def own_mentions(session: Session, person_id: int) -> list[MentionSpan]:
    """The person's own resolved mentions (not other people's)."""
    rows = session.execute(select(*_MENTION).where(EntityMentionRecord.person_id == person_id))
    return [MentionSpan(*row) for row in rows]


def linked_events(session: Session, person_id: int) -> list[EventSpan]:
    """The events linked to the person."""
    rows = session.execute(
        select(
            ExtractedEventRecord.id,
            ExtractedEventRecord.extraction_run_id,
            ExtractedEventRecord.event_type,
            ExtractedEventRecord.event_date,
            ExtractedEventRecord.confidence,
            ExtractedEventRecord.attributes,
            ExtractedEventRecord.start_offset,
            ExtractedEventRecord.end_offset,
        ).where(
            ExtractedEventRecord.id.in_(
                select(PersonEventLinkRecord.event_id).where(
                    PersonEventLinkRecord.person_id == person_id
                )
            )
        )
    )
    return [EventSpan(*row) for row in rows]


def legal_references(session: Session, run_ids: Collection[int]) -> list[MentionSpan]:
    """The legal-reference mentions of the extraction runs."""
    if not run_ids:
        return []
    rows = session.execute(
        select(*_MENTION).where(
            any_of(EntityMentionRecord.extraction_run_id, run_ids),
            EntityMentionRecord.entity_type == "legal_reference",
        )
    )
    return [MentionSpan(*row) for row in rows]


def run_articles(session: Session, run_ids: Collection[int]) -> dict[int, int]:
    """The article of each extraction run asked for."""
    if not run_ids:
        return {}
    return dict(
        session.execute(
            select(ArticleExtractionRunRecord.id, ArticleExtractionRunRecord.article_id).where(
                any_of(ArticleExtractionRunRecord.id, run_ids)
            )
        )
        .tuples()
        .all()
    )


def other_person_spans(
    session: Session, run_ids: Collection[int], person_id: int | None
) -> list[tuple[int, int, int]]:
    """(run, start, end) of the person mentions in the runs that are not the person's:
    unresolved, or resolved to someone else."""
    if not run_ids:
        return []
    rows = session.execute(
        select(
            EntityMentionRecord.extraction_run_id,
            EntityMentionRecord.start_offset,
            EntityMentionRecord.end_offset,
        ).where(
            any_of(EntityMentionRecord.extraction_run_id, run_ids),
            EntityMentionRecord.entity_type == "person",
            or_(EntityMentionRecord.person_id.is_(None), EntityMentionRecord.person_id != person_id)
            if person_id is not None
            else EntityMentionRecord.person_id.is_(None),
        )
    )
    return [(run_id, start, end) for run_id, start, end in rows]


@dataclass(frozen=True)
class LatestEvent:
    """A person's latest event (by its own date, then its publication's, unknown last, then
    the latest id)."""

    person_id: int
    article_id: int
    event_type: str
    event_date: datetime | None


def latest_events(session: Session, ids: Collection[int]) -> dict[int, LatestEvent]:
    """Per person asked for with a linked event, the latest of them.

    "Latest" is the event's own date where the text gave one, else the date its publication
    came out. Publication date is an ordering key here only; it is never the event's date.
    """
    if not ids:
        return {}
    ordered = sorted(set(ids))
    rows = session.execute(
        select(
            PersonEventLinkRecord.person_id,
            ExtractedEventRecord.id,
            ArticleExtractionRunRecord.article_id,
            ExtractedEventRecord.event_type,
            ExtractedEventRecord.event_date,
        )
        .join(ExtractedEventRecord, ExtractedEventRecord.id == PersonEventLinkRecord.event_id)
        .join(
            ArticleExtractionRunRecord,
            ArticleExtractionRunRecord.id == ExtractedEventRecord.extraction_run_id,
        )
        .where(any_of(PersonEventLinkRecord.person_id, ordered))
    ).all()
    days = published_days(session, {row.article_id for row in rows})
    latest: dict[int, tuple[tuple[Any, ...], LatestEvent]] = {}
    for person_id, event_id, article_id, event_type, event_date in rows:
        event = LatestEvent(person_id, article_id, event_type, event_date)
        rank = (
            event_date is None,
            -(event_date.timestamp() if event_date else 0.0),
            _sort_day(days.get(article_id)),
            -event_id,
        )
        if person_id not in latest or rank < latest[person_id][0]:
            latest[person_id] = (rank, event)
    return {person_id: event for person_id, (_, event) in latest.items()}


def mention_articles(session: Session, ids: Collection[int]) -> list[tuple[int, int, int]]:
    """(person, article, mention id) of every resolved mention of the people asked for."""
    if not ids:
        return []
    return [
        (person_id, article_id, mention_id)
        for person_id, article_id, mention_id in session.execute(
            select(
                EntityMentionRecord.person_id,
                ArticleExtractionRunRecord.article_id,
                EntityMentionRecord.id,
            )
            .join(
                ArticleExtractionRunRecord,
                ArticleExtractionRunRecord.id == EntityMentionRecord.extraction_run_id,
            )
            .where(any_of(EntityMentionRecord.person_id, ids))
        )
        if person_id is not None
    ]


def active_person_ids(session: Session, *, limit: int) -> list[int]:
    """The first `limit` active people, by id."""
    return list(
        session.scalars(
            select(PersonRecord.id)
            .where(PersonRecord.status == PersonStatus.ACTIVE.value)
            .order_by(PersonRecord.id)
            .limit(limit)
        ).all()
    )
