"""The analysis's verdicts as the results and the console read them (ADR 0031).

The tables are the analysis's (`analysis.storage`); another package reads them here, in
its own transaction, and gets values, never ORM records. Many groups at once.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Collection
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from analysis.persecution.queries import latest_persecution_classification_ids
from analysis.storage.models import (
    EntityGroupNewsRecord,
    EntityGroupPoliticsRecord,
    EntityGroupRfMatchRecord,
    PersecutionClassificationRecord,
    RosfinMatchRecord,
    UnnamedFigurantRecord,
    UnnamedIdentityResolutionRecord,
)
from db.arrays import any_of, any_text


@dataclass(frozen=True)
class PersonClassification:
    id: int
    person_id: int
    status: str
    confidence: float
    reasons: list[str]
    evidence_types: list[str]
    classifier_name: str
    classifier_version: str


def person_classification(session: Session, person_id: int) -> PersonClassification | None:
    """Latest ER v2 persecution classification, preserving its id tie-breaking rule."""
    row = session.scalars(
        select(PersecutionClassificationRecord).where(
            PersecutionClassificationRecord.person_id == person_id,
            PersecutionClassificationRecord.id.in_(latest_persecution_classification_ids()),
        )
    ).first()
    return None if row is None else _classification(row)


def _classification(row: PersecutionClassificationRecord) -> PersonClassification:
    return PersonClassification(
        row.id,
        row.person_id,
        row.status,
        row.confidence,
        list(row.reasons),
        list(row.evidence_types),
        row.classifier_name,
        row.classifier_version,
    )


def political_classifications(
    session: Session, *, min_confidence: float
) -> list[PersonClassification]:
    """The ER v2 people whose latest classification is political, at least that sure."""
    rows = session.scalars(
        select(PersecutionClassificationRecord).where(
            PersecutionClassificationRecord.id.in_(latest_persecution_classification_ids()),
            PersecutionClassificationRecord.status == "political",
            PersecutionClassificationRecord.confidence >= min_confidence,
        )
    )
    return [_classification(row) for row in rows]


def latest_classification_ids(session: Session, person_ids: Collection[int]) -> dict[int, int]:
    """Per person asked for, the id of its latest persecution classification."""
    if not person_ids:
        return {}
    rows = session.execute(
        select(PersecutionClassificationRecord.person_id, PersecutionClassificationRecord.id).where(
            PersecutionClassificationRecord.id.in_(latest_persecution_classification_ids()),
            any_of(PersecutionClassificationRecord.person_id, person_ids),
        )
    ).all()
    return {person_id: classification_id for person_id, classification_id in rows}


@dataclass(frozen=True)
class PersonMatch:
    id: int
    status: str
    confidence: float


def person_matches(
    session: Session, snapshot_id: int, person_ids: Collection[int]
) -> dict[int, PersonMatch]:
    """The ER v2 people's matches against one snapshot of the list."""
    if not person_ids:
        return {}
    rows = session.execute(
        select(
            RosfinMatchRecord.person_id,
            RosfinMatchRecord.id,
            RosfinMatchRecord.status,
            RosfinMatchRecord.confidence,
        ).where(
            RosfinMatchRecord.snapshot_id == snapshot_id,
            any_of(RosfinMatchRecord.person_id, person_ids),
        )
    ).all()
    return {person_id: PersonMatch(*rest) for person_id, *rest in rows}


@dataclass(frozen=True)
class PersonListMatch:
    snapshot_id: int
    status: str
    confidence: float
    matched_entry_id: int | None
    matched_entry_name: str | None
    reasons: list[str]


def person_list_match(session: Session, person_id: int) -> PersonListMatch | None:
    """Latest match by snapshot id then match id, as the person's detail card reads it."""
    row = session.scalars(
        select(RosfinMatchRecord)
        .where(RosfinMatchRecord.person_id == person_id)
        .order_by(RosfinMatchRecord.snapshot_id.desc(), RosfinMatchRecord.id.desc())
        .limit(1)
    ).first()
    return (
        None
        if row is None
        else PersonListMatch(
            row.snapshot_id,
            row.status,
            row.confidence,
            row.matched_entry_id,
            row.matched_entry_name,
            list(row.reasons),
        )
    )


def verdict_count(session: Session, *, verdict: str) -> int:
    """Groups with the requested political-classification verdict."""
    return (
        session.scalar(
            select(func.count())
            .select_from(EntityGroupPoliticsRecord)
            .where(EntityGroupPoliticsRecord.verdict == verdict)
        )
        or 0
    )


def verdict_reasons(session: Session, *, verdict: str) -> dict[int, str]:
    """Reasons by group for a verdict's review queue; one bulk read."""
    return dict(
        session.execute(
            select(EntityGroupPoliticsRecord.group_id, EntityGroupPoliticsRecord.reason).where(
                EntityGroupPoliticsRecord.verdict == verdict
            )
        )
        .tuples()
        .all()
    )


_OPEN_UNNAMED = text(
    """
    SELECT count(*) FROM unnamed_figurants f
    WHERE f.age IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM unnamed_identity_resolutions r
        WHERE r.figurant_key = f.key
          AND r.resolution IN ('rf_entry', 'existing_person', 'supplied_name', 'insufficient')
    )
    """
)


def unidentified_figurant_count(session: Session) -> int:
    """Age-known figurants still in the identification queue; no_rf_match stays open."""
    return session.scalar(_OPEN_UNNAMED) or 0


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


def verdicts(session: Session) -> dict[int, str]:
    """Every group's politics verdict, and nothing else of it."""
    return dict(
        session.execute(
            select(EntityGroupPoliticsRecord.group_id, EntityGroupPoliticsRecord.verdict)
        )
        .tuples()
        .all()
    )


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


@dataclass(frozen=True)
class Figurant:
    """An unnamed figurant: a quote of an article that describes a person it does not name."""

    id: int
    key: str
    article_id: int
    start_offset: int
    end_offset: int
    age: int | None
    gender: str | None
    place: str
    initial: str | None
    articles: list[Any]
    event_type: str
    quote: str
    explanation: str
    published_at: datetime | None


_FIGURANT = (
    UnnamedFigurantRecord.id,
    UnnamedFigurantRecord.key,
    UnnamedFigurantRecord.article_id,
    UnnamedFigurantRecord.start_offset,
    UnnamedFigurantRecord.end_offset,
    UnnamedFigurantRecord.age,
    UnnamedFigurantRecord.gender,
    UnnamedFigurantRecord.place,
    UnnamedFigurantRecord.initial,
    UnnamedFigurantRecord.articles,
    UnnamedFigurantRecord.event_type,
    UnnamedFigurantRecord.quote,
    UnnamedFigurantRecord.explanation,
    UnnamedFigurantRecord.published_at,
)


def article_figurants(session: Session, article_id: int) -> list[Figurant]:
    """The unnamed figurants of one article, in text order."""
    rows = session.execute(
        select(*_FIGURANT)
        .where(UnnamedFigurantRecord.article_id == article_id)
        .order_by(UnnamedFigurantRecord.start_offset)
    ).all()
    return [Figurant(*row) for row in rows]


def open_figurants(session: Session, *, closed: Collection[str]) -> list[Figurant]:
    """The unnamed figurants without a resolution in `closed`, in id order."""
    resolved = select(UnnamedIdentityResolutionRecord.figurant_key).where(
        UnnamedIdentityResolutionRecord.figurant_key == UnnamedFigurantRecord.key,
        any_text(UnnamedIdentityResolutionRecord.resolution, closed, name="closed"),
    )
    rows = session.execute(
        select(*_FIGURANT).where(~resolved.exists()).order_by(UnnamedFigurantRecord.id)
    ).all()
    return [Figurant(*row) for row in rows]
