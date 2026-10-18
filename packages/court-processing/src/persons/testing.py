"""ER v2's explicit testing API: fixtures of its tables for the evaluation and tests.

Not for production code (`tests/test_data_ownership.py` holds every production module
to it; ADR 0031, «Evaluation»).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from persons.storage.models import (
    PersonAliasRecord,
    PersonEventLinkRecord,
    PersonRecord,
    PersonResolutionDecisionRecord,
)

# The tables a disposable test or evaluation database is wiped of between runs
# (`db.maintenance.truncate_disposable_tables`); a cache of model answers worth keeping
# is not among them.
DISPOSABLE_TABLES = (
    "person_resolution_decisions",
    "person_event_links",
    "review_records",
    "person_merges",
    "person_aliases",
    "persons",
)


def add_person(
    session: Session,
    *,
    canonical_name: str,
    normalized_name: str,
    matching_key: str,
    aliases: Sequence[tuple[str, str, str]] = (),
) -> int:
    """An active person with manual aliases (surface, normalized, matching key); its id."""
    record = PersonRecord(
        canonical_name=canonical_name,
        normalized_name=normalized_name,
        matching_key=matching_key,
        status="active",
    )
    session.add(record)
    session.flush()
    for surface, normalized, key in aliases:
        session.add(
            PersonAliasRecord(
                person_id=record.id,
                surface_text=surface,
                normalized_text=normalized,
                matching_key=key,
                origin="manual",
                confidence=1.0,
            )
        )
    return record.id


def link_event(session: Session, person_id: int, event_id: int, *, role: str) -> None:
    """The person as a party to the event, beyond doubt."""
    session.add(
        PersonEventLinkRecord(person_id=person_id, event_id=event_id, role=role, confidence=1.0)
    )


def reidentify(
    session: Session, person_id: int, identity: Callable[[str], tuple[str, str | None]]
) -> None:
    """Store the person's name and its aliases as `identity` (surface to normalized name
    and matching key) reads them, as the pipeline would have stored them."""
    record = session.get(PersonRecord, person_id)
    assert record is not None
    name, key = identity(record.canonical_name)
    session.execute(
        update(PersonRecord)
        .where(PersonRecord.id == person_id)
        .values(normalized_name=name, matching_key=key)
    )
    for alias in session.scalars(
        select(PersonAliasRecord).where(PersonAliasRecord.person_id == person_id)
    ).all():
        alias_name, alias_key = identity(alias.surface_text)
        alias.normalized_text = alias_name
        alias.matching_key = alias_key or ""


def resolution_decisions(session: Session) -> dict[int, tuple[str, str]]:
    """Per mention, the ER v2 decision's action and status."""
    return {
        mention_id: (action, status)
        for mention_id, action, status in session.execute(
            select(
                PersonResolutionDecisionRecord.mention_id,
                PersonResolutionDecisionRecord.action,
                PersonResolutionDecisionRecord.status,
            )
        )
    }


def decision_rows(session: Session) -> list[tuple[int, str, str, int | None, Any]]:
    """(mention, action, status, selected person, stored candidates) of every ER v2
    decision, by id."""
    return [
        (row[0], row[1], row[2], row[3], row[4])
        for row in session.execute(
            select(
                PersonResolutionDecisionRecord.mention_id,
                PersonResolutionDecisionRecord.action,
                PersonResolutionDecisionRecord.status,
                PersonResolutionDecisionRecord.selected_person_id,
                PersonResolutionDecisionRecord.candidates,
            ).order_by(PersonResolutionDecisionRecord.id)
        )
    ]


def event_links(session: Session) -> list[tuple[int, int]]:
    """(event, person) of every ER v2 event link."""
    return [
        (event_id, person_id)
        for event_id, person_id in session.execute(
            select(PersonEventLinkRecord.event_id, PersonEventLinkRecord.person_id)
        )
    ]


def person_statuses(session: Session) -> dict[int, str]:
    return dict(session.execute(select(PersonRecord.id, PersonRecord.status)).tuples().all())
