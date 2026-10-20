"""The criminal cases as the console and the API read them (ADR 0031): values, never ORM.

A case with no active link (its events and articles are gone) is not listed. People and
publications come from their owners' APIs; the case stores only the person's key.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from cases.storage.models import (
    CaseEventLinkRecord,
    CaseParticipantRecord,
    CriminalCaseRecord,
)
from ingestion import read as ingestion_read
from processing import read as processing_read


@dataclass(frozen=True)
class CaseSummary:
    id: UUID
    created_at: datetime
    preliminary: bool
    case_number: str | None
    description: str
    algorithm_version: str
    events: int
    articles: int
    # Opened apart from a case it matched but could not be told from: a person may check it.
    ambiguous: bool


@dataclass(frozen=True)
class CaseEventView:
    """An event of the case and why it is there. `score` and `reasons` are empty for the
    event that opened the case; `candidates` are the cases it was not joined to."""

    evidence_key: str
    event_id: int | None
    article_id: int
    event_type: str
    event_date: date | None
    score: float
    reasons: tuple[str, ...]
    candidates: tuple[dict[str, Any], ...]
    algorithm_version: str


@dataclass(frozen=True)
class CaseParticipantView:
    person_key: str
    # None: no group of this key exists now (renamed by a rebuild, or removed).
    name: str | None
    role: str | None
    # The events that name the person in this case.
    evidence_keys: tuple[str, ...]


@dataclass(frozen=True)
class CaseSource:
    article_id: int
    title: str
    url: str
    published_at: datetime | None
    source_name: str


@dataclass(frozen=True)
class CaseDetail:
    summary: CaseSummary
    events: tuple[CaseEventView, ...]
    participants: tuple[CaseParticipantView, ...]
    sources: tuple[CaseSource, ...]


def _summaries(session: Session, query: Select[Any]) -> list[CaseSummary]:
    return [
        CaseSummary(
            id=case.id,
            created_at=case.created_at,
            preliminary=case.preliminary,
            case_number=case.case_number,
            description=case.description,
            algorithm_version=case.algorithm_version,
            events=events,
            articles=articles,
            ambiguous=ambiguous,
        )
        for case, events, articles, ambiguous in session.execute(query)
    ]


def _listing() -> Select[Any]:
    """Cases with their active links counted, newest first."""
    return (
        select(
            CriminalCaseRecord,
            func.count(CaseEventLinkRecord.evidence_key),
            func.count(func.distinct(CaseEventLinkRecord.article_id)),
            func.coalesce(
                func.bool_or(func.jsonb_array_length(CaseEventLinkRecord.candidates) > 0), False
            ),
        )
        .join(CaseEventLinkRecord, CaseEventLinkRecord.case_id == CriminalCaseRecord.id)
        .where(CaseEventLinkRecord.active.is_(True))
        .group_by(CriminalCaseRecord.id)
        .order_by(CriminalCaseRecord.created_at.desc(), CriminalCaseRecord.id)
    )


def list_cases(
    session: Session, *, limit: int, offset: int = 0, ambiguous_only: bool = False
) -> tuple[int, list[CaseSummary]]:
    """(the number of cases, one page of them)."""
    query = _listing()
    if ambiguous_only:
        query = query.having(
            func.bool_or(func.jsonb_array_length(CaseEventLinkRecord.candidates) > 0)
        )
    total = session.scalar(select(func.count()).select_from(query.order_by(None).subquery())) or 0
    return total, _summaries(session, query.limit(limit).offset(offset))


def cases_of_person(session: Session, person_key: str) -> list[CaseSummary]:
    """The cases that name the person (`entity_groups.key`), newest first."""
    ids = select(CaseEventLinkRecord.case_id).where(
        CaseEventLinkRecord.active.is_(True), CaseEventLinkRecord.targets.contains([person_key])
    )
    return _summaries(session, _listing().where(CriminalCaseRecord.id.in_(ids)))


def case_detail(session: Session, case_id: UUID) -> CaseDetail | None:
    found = _summaries(session, _listing().where(CriminalCaseRecord.id == case_id))
    if not found:
        return None
    links = session.scalars(
        select(CaseEventLinkRecord)
        .where(CaseEventLinkRecord.case_id == case_id, CaseEventLinkRecord.active.is_(True))
        .order_by(CaseEventLinkRecord.resolved_at, CaseEventLinkRecord.evidence_key)
    ).all()
    events = tuple(
        sorted(
            (
                CaseEventView(
                    evidence_key=link.evidence_key,
                    event_id=link.event_id,
                    article_id=link.article_id,
                    event_type=link.evidence["event_type"],
                    event_date=date.fromisoformat(link.evidence["event_date"])
                    if link.evidence["event_date"]
                    else None,
                    score=link.score,
                    reasons=tuple(link.reasons),
                    candidates=tuple(link.candidates),
                    algorithm_version=link.algorithm_version,
                )
                for link in links
            ),
            key=lambda view: (
                view.event_date is None,
                view.event_date or date.max,
                view.evidence_key,
            ),
        )
    )
    named: dict[str, list[str]] = {}
    roles: dict[str, str | None] = {}
    for evidence_key, person_key, role in session.execute(
        select(
            CaseParticipantRecord.evidence_key,
            CaseParticipantRecord.person_key,
            CaseParticipantRecord.role,
        ).where(CaseParticipantRecord.evidence_key.in_([link.evidence_key for link in links]))
    ):
        named.setdefault(person_key, []).append(evidence_key)
        roles[person_key] = roles.get(person_key) or role
    groups = processing_read.groups_by_key(session, named)
    participants = tuple(
        CaseParticipantView(
            person_key=person_key,
            name=groups[person_key].name if person_key in groups else None,
            role=roles[person_key],
            evidence_keys=tuple(sorted(keys)),
        )
        for person_key, keys in sorted(named.items())
    )
    cards = ingestion_read.article_cards(session, {link.article_id for link in links})
    sources = tuple(
        CaseSource(card.id, card.title, card.url, card.published_at, card.source_name)
        for card in sorted(cards.values(), key=lambda card: card.id)
    )
    return CaseDetail(found[0], events, participants, sources)
