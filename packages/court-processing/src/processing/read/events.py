"""Extracted events and their people, mentions and publication counts."""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from db.arrays import any_of
from processing.storage.models import (
    EntityMentionRecord,
    EventEntityMentionRecord,
)


@dataclass(frozen=True)
class TargetEvent:
    """An extracted event whose target is one of a group's mentions."""

    id: int
    event_type: str
    event_date: datetime | None
    confidence: float | None
    extractor_name: str
    start_offset: int
    end_offset: int
    article_id: int


_TARGET_EVENTS = text(
    """
    SELECT DISTINCT e.id, e.event_type, e.event_date, e.confidence, e.extractor_name,
           e.start_offset, e.end_offset, r.article_id
    FROM entity_group_mentions gm
    JOIN event_entity_mentions em ON em.mention_id = gm.mention_id AND em.role = 'target'
    JOIN extracted_events e ON e.id = em.event_id
    JOIN article_extraction_runs r ON r.id = e.extraction_run_id
    WHERE gm.group_id = :group
    """
)


def target_events(session: Session, group_id: int) -> list[TargetEvent]:
    """Every event a group's mention is the target of, each once; in no order."""
    return [TargetEvent(*row) for row in session.execute(_TARGET_EVENTS, {"group": group_id})]


def event_orgs(session: Session, event_ids: Collection[int]) -> list[tuple[int, str, str]]:
    """(event, role, as the text wrote it) of the courts and bodies the events name."""
    if not event_ids:
        return []
    return [
        (event_id, role, surface)
        for event_id, role, surface in session.execute(
            select(
                EventEntityMentionRecord.event_id,
                EventEntityMentionRecord.role,
                EntityMentionRecord.surface_text,
            )
            .join(
                EntityMentionRecord, EntityMentionRecord.id == EventEntityMentionRecord.mention_id
            )
            .where(
                any_of(EventEntityMentionRecord.event_id, event_ids),
                EventEntityMentionRecord.role.in_(("court", "authority")),
            )
        ).all()
    ]


_WITH_EVENTS = text(
    """
    WITH latest AS (
        -- Only the article's latest successful run, as the collector takes it. A
        -- re-extraction opens a new run and keeps the old one, so reading every run
        -- keeps the articles a later extractor no longer finds a case in.
        SELECT DISTINCT ON (article_id) id, article_id
        FROM article_extraction_runs
        WHERE status = 'succeeded'
        ORDER BY article_id, id DESC
    )
    SELECT DISTINCT r.article_id FROM latest r
    JOIN extracted_events e ON e.extraction_run_id = r.id
    WHERE e.event_type = ANY(:types)
    ORDER BY r.article_id
    """
)


def articles_with_events(session: Session, event_types: Collection[str]) -> list[int]:
    """The articles whose latest successful extraction found an event of these types, by id."""
    return list(session.scalars(_WITH_EVENTS, {"types": list(event_types)}))


_EVENT_COUNTS = text(
    """
    SELECT r.article_id, e.event_type, count(*)
    FROM extracted_events e
    JOIN (
        -- Only the article's latest successful run, as the collector takes it. A
        -- re-extraction opens a new run and keeps the old one, so counting every run
        -- counts one event twice — and keeps counting events a later extractor no
        -- longer finds.
        SELECT DISTINCT ON (article_id) id, article_id
        FROM article_extraction_runs
        WHERE status = 'succeeded'
        ORDER BY article_id, id DESC
    ) r ON r.id = e.extraction_run_id
    WHERE r.article_id = ANY(:articles)
    GROUP BY r.article_id, e.event_type
    """
)


def event_counts(session: Session, article_ids: Collection[int]) -> list[tuple[int, str, int]]:
    """(article, event type, how many) of the articles' latest successful extraction."""
    if not article_ids:
        return []
    return [
        (row[0], row[1], row[2])
        for row in session.execute(_EVENT_COUNTS, {"articles": sorted(set(article_ids))})
    ]


@dataclass(frozen=True)
class EventFact:
    """An extracted event, with the day the text wrote for it (None: it wrote none).

    `event_date` is the event's own date only. The article's `published_at` is a separate
    thing and is never merged into it: an event whose text names no date has none, and
    `event_day` is None with it.
    """

    id: int
    event_type: str
    event_date: datetime | None
    confidence: float
    start_offset: int
    end_offset: int
    article_id: int
    # `event_date` as a day of the database's time zone.
    event_day: date | None


_EVENT_FACTS = text(
    """
    SELECT e.id, e.event_type, e.event_date, e.confidence, e.start_offset, e.end_offset,
           r.article_id, e.event_date::date
    FROM extracted_events e JOIN article_extraction_runs r ON r.id = e.extraction_run_id
    WHERE e.id = ANY(:events)
    """
)


_EVENTS_NAMING = text(
    """
    SELECT em.event_id
    FROM entity_group_mentions gm
    JOIN event_entity_mentions em ON em.mention_id = gm.mention_id
    WHERE gm.group_id = :group
    """
)


_EVENTS_DATED = text(
    """
    WITH latest AS (
        -- Only the article's latest successful run, as the collector takes it. A
        -- re-extraction opens a new run and keeps the old one, so reading every run
        -- counts one event as two reports of it.
        SELECT DISTINCT ON (article_id) id, article_id
        FROM article_extraction_runs
        WHERE status = 'succeeded'
        ORDER BY article_id, id DESC
    )
    SELECT e.id FROM extracted_events e
    JOIN latest r ON r.id = e.extraction_run_id
    JOIN unnest(CAST(:types AS text[]), CAST(:days AS date[])) AS k(event_type, day)
      ON k.event_type = e.event_type AND k.day = e.event_date::date
    """
)


_EVENTS_UNDATED = text(
    """
    WITH latest AS (
        -- Only the article's latest successful run, as the collector takes it. A
        -- re-extraction opens a new run and keeps the old one, so reading every run
        -- counts one event as two reports of it.
        SELECT DISTINCT ON (article_id) id, article_id
        FROM article_extraction_runs
        WHERE status = 'succeeded'
        ORDER BY article_id, id DESC
    )
    SELECT e.id FROM extracted_events e
    JOIN latest r ON r.id = e.extraction_run_id
    WHERE e.event_date IS NULL AND r.article_id = ANY(:articles) AND e.event_type = ANY(:types)
    """
)


_EVENT_PEOPLE = text(
    """
    SELECT e.id, gm.group_id, em.role, coalesce(ro.role <> 'mentioned', true) AS binds
    FROM extracted_events e
    LEFT JOIN event_entity_mentions em ON em.event_id = e.id
    LEFT JOIN entity_group_mentions gm ON gm.mention_id = em.mention_id
    LEFT JOIN entity_group_roles ro ON ro.group_id = gm.group_id
    WHERE e.id = ANY(:events)
    """
)


# Everything the reports of an event name, with the person a name was resolved to (if it
# was).
_EVENT_MENTIONS = text(
    """
    SELECT em.role, m.entity_type, m.surface_text, m.normalized_text, m.normalized_data,
           g.id AS group_id, g.key, g.name
    FROM event_entity_mentions em
    JOIN entity_mentions m ON m.id = em.mention_id
    LEFT JOIN entity_group_mentions gm ON gm.mention_id = m.id
    LEFT JOIN entity_groups g ON g.id = gm.group_id
    WHERE em.event_id = ANY(:events)
    ORDER BY em.role, em.event_id, m.start_offset, m.id
    """
)


@dataclass(frozen=True)
class EventPerson:
    """Who an event names (a group, when the name was resolved to one) and as what;
    `binds`: the group is more than mentioned in its cases."""

    event_id: int
    group_id: int | None
    role: str | None
    binds: bool | None


@dataclass(frozen=True)
class EventMention:
    """A mention an event names, with the group it was resolved to (if it was)."""

    role: str
    entity_type: str
    surface_text: str | None
    normalized_text: str | None
    normalized_data: Any
    group_id: int | None
    key: str | None
    name: str | None


def event_facts(session: Session, ids: Collection[int]) -> dict[int, EventFact]:
    if not ids:
        return {}
    return {
        row[0]: EventFact(*row)
        for row in session.execute(_EVENT_FACTS, {"events": sorted(set(ids))}).all()
    }


def events_naming(session: Session, group_id: int) -> list[int]:
    """The events one of the group's mentions takes a part in, whatever the part."""
    return list(session.scalars(_EVENTS_NAMING, {"group": group_id}))


def events_dated(session: Session, keys: Collection[tuple[str, date]]) -> list[int]:
    """The events of these (type, day), by the day their own date names.

    Only the articles' latest successful runs: an older run's event is a report of an
    event the current extraction no longer tells of."""
    if not keys:
        return []
    ordered = sorted(keys)
    return list(
        session.scalars(
            _EVENTS_DATED,
            {"types": [key[0] for key in ordered], "days": [key[1] for key in ordered]},
        )
    )


def events_undated(
    session: Session, article_ids: Collection[int], types: Collection[str]
) -> list[int]:
    """The events without a date of their own, of these types, in these articles.

    Only the articles' latest successful runs: an older run's event is a report of an
    event the current extraction no longer tells of."""
    if not article_ids or not types:
        return []
    return list(
        session.scalars(
            _EVENTS_UNDATED, {"articles": sorted(set(article_ids)), "types": sorted(set(types))}
        )
    )


def event_people(session: Session, ids: Collection[int]) -> list[EventPerson]:
    """For each event, who it names (a row with no group when a name was not resolved,
    and one with nothing when it names nobody)."""
    if not ids:
        return []
    return [
        EventPerson(*row) for row in session.execute(_EVENT_PEOPLE, {"events": sorted(set(ids))})
    ]


def event_mentions(session: Session, ids: Collection[int]) -> list[EventMention]:
    """Everything the events name, by role, event, place in the text."""
    if not ids:
        return []
    return [
        EventMention(*row) for row in session.execute(_EVENT_MENTIONS, {"events": sorted(set(ids))})
    ]
