"""The extracted events with what case resolution scores them by (ADR 0031).

One reader for the criminal-case stage: the event, the people it is about (groups the
roles step has not set aside as only mentioned), the Criminal Code articles, the courts,
the bodies and the places it names, and the sentence of the article it stands in. Values
only, never ORM records. Ids of events and groups change on a rebuild, so the people come
as `entity_groups.key` and the event is found again by its article and its words.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Collection
from dataclasses import dataclass
from datetime import date

from sqlalchemy import text
from sqlalchemy.orm import Session

from ingestion import read as ingestion_read
from ingestion.read import TextWindow

# Events that carry no step of a criminal case (`EventType.OTHER`) are left out.
CASE_EVENT_TYPES = (
    "case_opened",
    "search",
    "detention",
    "arrest",
    "charge",
    "sentence",
    "fine",
    "release",
)


@dataclass(frozen=True)
class TargetPerson:
    """A group an event is about: its key, its name, and its role in cases (None: the roles
    step has not decided)."""

    key: str
    name: str
    role: str | None


@dataclass(frozen=True)
class CaseEvent:
    id: int
    article_id: int
    event_type: str
    # The event's own day; None when the text names none (never the article's day).
    event_date: date | None
    # The words of the event, as the article wrote them.
    quote: str
    targets: tuple[TargetPerson, ...]
    # «205.2» or «205.2/3»: article and, when the text gives it, the part.
    charges: tuple[str, ...]
    courts: tuple[str, ...]
    authorities: tuple[str, ...]
    places: tuple[str, ...]


_EVENT_IDS = text(
    """
    SELECT e.id FROM extracted_events e
    WHERE e.id > :after AND e.event_type = ANY(:types)
    ORDER BY e.id LIMIT :limit
    """
)
_EVENTS = text(
    """
    SELECT e.id, r.article_id, e.event_type, e.event_date::date, e.start_offset, e.end_offset
    FROM extracted_events e JOIN article_extraction_runs r ON r.id = e.extraction_run_id
    WHERE e.id = ANY(:events)
    """
)
# Who a person-target mention was resolved to; a group set aside as «mentioned» (a judge, a
# lawyer, a bystander) is no target of a case.
_TARGETS = text(
    """
    SELECT DISTINCT em.event_id, g.key, g.name, ro.role
    FROM event_entity_mentions em
    JOIN entity_group_mentions gm ON gm.mention_id = em.mention_id
    JOIN entity_groups g ON g.id = gm.group_id
    LEFT JOIN entity_group_roles ro ON ro.group_id = g.id
    WHERE em.event_id = ANY(:events) AND em.role = 'target'
      AND coalesce(ro.role <> 'mentioned', true)
      AND NOT EXISTS (
          SELECT 1 FROM entity_official_marks om WHERE om.key = g.key AND om.official
      )
    """
)
_CHARGES = text(
    """
    SELECT DISTINCT event_id, article, part FROM entity_group_charges
    WHERE event_id = ANY(:events)
    """
)
_ORGANISATIONS = text(
    """
    SELECT DISTINCT em.event_id, em.role, lower(m.normalized_text)
    FROM event_entity_mentions em JOIN entity_mentions m ON m.id = em.mention_id
    WHERE em.event_id = ANY(:events) AND em.role IN ('court', 'authority', 'location')
    """
)


def event_ids_after(session: Session, *, after: int, limit: int) -> list[int]:
    """Ids of the case events above `after`, lowest first: one batch of a run."""
    return list(
        session.scalars(
            _EVENT_IDS, {"after": after, "types": list(CASE_EVENT_TYPES), "limit": limit}
        )
    )


def case_events(session: Session, ids: Collection[int]) -> list[CaseEvent]:
    """The events asked for that still exist, in id order."""
    wanted = sorted(set(ids))
    if not wanted:
        return []
    params = {"events": wanted}
    rows = session.execute(_EVENTS, params).all()
    targets: dict[int, list[TargetPerson]] = defaultdict(list)
    for event_id, key, name, role in session.execute(_TARGETS, params):
        targets[event_id].append(TargetPerson(key, name, role))
    charges: dict[int, set[str]] = defaultdict(set)
    for event_id, article, part in session.execute(_CHARGES, params):
        charges[event_id].add(f"{article}/{part}" if part else article)
    organisations: dict[tuple[int, str], set[str]] = defaultdict(set)
    for event_id, role, value in session.execute(_ORGANISATIONS, params):
        organisations[(event_id, role)].add(value)
    quotes = ingestion_read.text_windows(
        session, [TextWindow(row[1], row[4], row[5] - row[4]) for row in rows]
    )
    return [
        CaseEvent(
            id=event_id,
            article_id=article_id,
            event_type=event_type,
            event_date=event_date,
            quote=(quote or "").strip(),
            targets=tuple(sorted(targets[event_id], key=lambda person: person.key)),
            charges=tuple(sorted(charges[event_id])),
            courts=tuple(sorted(organisations[(event_id, "court")])),
            authorities=tuple(sorted(organisations[(event_id, "authority")])),
            places=tuple(sorted(organisations[(event_id, "location")])),
        )
        for (event_id, article_id, event_type, event_date, _, _), quote in sorted(
            zip(rows, quotes, strict=True), key=lambda pair: pair[0][0]
        )
    ]
