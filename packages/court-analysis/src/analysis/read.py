"""The analysis's verdicts as the results and the console read them (ADR 0031).

The tables are the analysis's (`analysis.storage`); another package reads them here, in
its own transaction, and gets values, never ORM records. Many groups at once.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Collection
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from analysis.storage.models import (
    EntityGroupNewsRecord,
    EntityGroupPoliticsRecord,
    EntityGroupRfMatchRecord,
)
from db.arrays import any_of


@dataclass(frozen=True)
class Politics:
    """Whether a figurant's case is political (step 5), and why."""

    group_id: int
    verdict: str
    method: str
    reason: str
    quote: str


@dataclass(frozen=True)
class CaseNews:
    """What a political case's latest publication tells (step 5)."""

    group_id: int
    kind: str
    method: str
    reason: str
    quote: str
    published_at: datetime | None


@dataclass(frozen=True)
class ListEntry:
    """An entry of the Rosfinmonitoring list a group's name matched."""

    full_name: str
    birth_date: date


def politics(session: Session, groups: Collection[int] | None = None) -> dict[int, Politics]:
    """The politics verdicts of the groups asked for (None: every verdict)."""
    if groups is not None and not groups:
        return {}
    query = select(EntityGroupPoliticsRecord)
    if groups is not None:
        query = query.where(any_of(EntityGroupPoliticsRecord.group_id, groups))
    return {
        row.group_id: Politics(row.group_id, row.verdict, row.method, row.reason, row.quote)
        for row in session.scalars(query)
    }


def groups_with_verdict(session: Session, verdict: str) -> list[int]:
    """The groups of one politics verdict («political», …), in id order."""
    return list(
        session.scalars(
            select(EntityGroupPoliticsRecord.group_id)
            .where(EntityGroupPoliticsRecord.verdict == verdict)
            .order_by(EntityGroupPoliticsRecord.group_id)
        )
    )


def case_news(session: Session, groups: Collection[int] | None = None) -> dict[int, CaseNews]:
    """The latest news of the cases asked for (None: every case with news)."""
    if groups is not None and not groups:
        return {}
    query = select(EntityGroupNewsRecord)
    if groups is not None:
        query = query.where(any_of(EntityGroupNewsRecord.group_id, groups))
    return {
        row.group_id: CaseNews(
            row.group_id, row.kind, row.method, row.reason, row.quote, row.published_at
        )
        for row in session.scalars(query)
    }


def list_levels(session: Session, groups: Collection[int] | None = None) -> dict[int, set[str]]:
    """Per group, the levels of its matches with the list («full», «name», …)."""
    if groups is not None and not groups:
        return {}
    query = select(EntityGroupRfMatchRecord.group_id, EntityGroupRfMatchRecord.level)
    if groups is not None:
        query = query.where(any_of(EntityGroupRfMatchRecord.group_id, groups))
    found: dict[int, set[str]] = defaultdict(set)
    for group_id, level in session.execute(query).all():
        found[group_id].add(level)
    return dict(found)


_LIST_ENTRIES = text(
    """
    SELECT DISTINCT e.full_name, e.birth_date::date AS birth_date
    FROM entity_group_rf_matches m
    JOIN rosfinmonitoring_entries e ON e.id = m.entry_id
    WHERE m.group_id = :group_id AND e.birth_date IS NOT NULL
    ORDER BY e.full_name, birth_date
    """
)


def list_entries(session: Session, group_id: int) -> list[ListEntry]:
    """The entries with a birth date the group's name matched, by name then birth date
    (in the database's collation), each once."""
    return [
        ListEntry(row.full_name, row.birth_date)
        for row in session.execute(_LIST_ENTRIES, {"group_id": group_id})
    ]


@dataclass(frozen=True)
class ListMatch:
    """A match of a group's name with an entry of the list, and where the entry stands."""

    level: str
    full_name: str
    birth_date: datetime | None
    birth_place: str | None
    snapshot_date: datetime
    inclusion_date: datetime | None
    inclusion_source: str | None


_LIST_MATCHES = text(
    """
    SELECT m.level, e.full_name, e.birth_date, e.birth_place, s.snapshot_date, e.inclusion_date,
           e.inclusion_source
    FROM entity_group_rf_matches m
    JOIN rosfinmonitoring_entries e ON e.id = m.entry_id
    JOIN rosfinmonitoring_snapshots s ON s.id = e.snapshot_id
    WHERE m.group_id = :group
    UNION ALL
    SELECT NULL, NULL, NULL, NULL, max(snapshot_date), NULL, NULL
    FROM rosfinmonitoring_snapshots
    ORDER BY 1 NULLS LAST, 2
    """
)


def list_matches(session: Session, group_id: int) -> tuple[list[ListMatch], datetime | None]:
    """The group's matches with the list (by level, then name, in the database's order),
    and the date of the latest snapshot the check could read."""
    rows = session.execute(_LIST_MATCHES, {"group": group_id}).all()
    matches = [ListMatch(*row) for row in rows if row.level is not None]
    latest = next((row.snapshot_date for row in rows if row.level is None), None)
    return matches, latest
