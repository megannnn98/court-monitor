"""Case resolution: attach the extracted events to criminal cases, a batch at a time.

The one public command is `resolve`. It is safe to repeat: an event already tied to a case
is skipped, an event processing has re-extracted is found again by its `evidence_key` (the
article, the step and the words, none of which a rebuild changes) and tied back to its case,
and a case id is never given twice. Two runs never overlap: each batch holds one advisory
lock until its transaction ends. Cases are never merged, and a link never moves to another
case because of a better match later; only an event whose person is no longer the case's
moves out, to a case of its own.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import CursorResult, delete, func, select, update
from sqlalchemy.dialects.postgresql import array
from sqlalchemy.orm import Session, sessionmaker

from cases import matching
from cases.matching import Evidence, Match
from cases.storage.models import (
    CaseEventLinkRecord,
    CaseParticipantRecord,
    CriminalCaseRecord,
)
from processing import case_events
from processing.case_events import CaseEvent

LOCK_KEY = "criminal-case-resolution"
# Candidates kept on a link that was not joined to any of them.
MAX_RECORDED_CANDIDATES = 10
# Below this a case shares nothing worth recording but the person.
_RECORDED_SCORE = 0.40
_SPACES = re.compile(r"\s+")


@dataclass(frozen=True)
class ResolutionReport:
    events_seen: int = 0
    # Events already tied to a case, left as they are.
    unchanged: int = 0
    # Events found again after a rebuild and tied back to their old link.
    reattached: int = 0
    joined: int = 0
    opened: int = 0
    # Of the opened cases, those that had a candidate they were not joined to.
    ambiguous: int = 0
    moved_out: int = 0
    deactivated: int = 0
    # Events that name nobody: no case is formed for them, the link records that they
    # were looked at (ADR 0033).
    without_case: int = 0
    batches: int = 0
    dry_run: bool = False


def evidence_key(event: CaseEvent, *, ordinal: int = 0) -> str:
    """Stable across rebuilds: the article, the step and its words; `ordinal` tells apart
    the same sentence told twice by one article."""
    words = _SPACES.sub(" ", event.quote.lower()).strip()
    raw = f"{event.article_id}|{event.event_type}|{words}|{ordinal}"
    return hashlib.sha256(raw.encode()).hexdigest()


def evidence_of(event: CaseEvent) -> Evidence:
    words = _SPACES.sub(" ", event.quote.lower()).strip()
    return Evidence(
        event_type=event.event_type,
        event_date=event.event_date,
        targets=frozenset(person.key for person in event.targets),
        charges=frozenset(event.charges),
        courts=frozenset(event.courts),
        authorities=frozenset(event.authorities),
        places=frozenset(event.places),
        case_number=None,
        quote_hash=hashlib.sha256(words.encode()).hexdigest(),
        quote_length=len(words),
    )


def rollback_link(session: Session, evidence_key: str, *, author: str, reason: str) -> bool:
    """Roll a link back: the link stops counting, and the decision is kept.

    Nothing is deleted: the row stays with `active = false`, the status `rejected`, and
    who decided, when and why. The events and the cases themselves are untouched.
    """
    if not author or not reason:
        raise ValueError("a rollback needs an author and a reason")
    link = session.get(CaseEventLinkRecord, evidence_key)
    if link is None:
        return False
    link.active = False
    link.status = "rejected"
    link.decided_by = author
    link.decided_at = datetime.now(UTC)
    link.decision_note = reason
    return True


def resolve(
    session_factory: sessionmaker[Session], *, batch_size: int = 500, dry_run: bool = False
) -> ResolutionReport:
    """Tie every case event that has no active link to a case; with `dry_run` nothing is
    written (the work is rolled back at the end, so later batches still see earlier ones)."""
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    report = ResolutionReport(dry_run=dry_run)
    with session_factory() as session:
        after = 0
        while True:
            if not dry_run:
                session.execute(select(func.pg_advisory_xact_lock(func.hashtext(LOCK_KEY))))
            ids = case_events.event_ids_after(session, after=after, limit=batch_size)
            if not ids:
                break
            after = ids[-1]
            report = _resolve_batch(session, ids, report)
            if not dry_run:
                session.commit()
        if not dry_run:
            report = _deactivate_orphans(session, report)
            session.commit()
        else:
            session.rollback()
    return report


def _resolve_batch(
    session: Session, ids: Sequence[int], report: ResolutionReport
) -> ResolutionReport:
    linked = set(
        session.scalars(
            select(CaseEventLinkRecord.event_id).where(
                CaseEventLinkRecord.event_id.in_(ids), CaseEventLinkRecord.active.is_(True)
            )
        )
    )
    events = case_events.case_events(
        session, [event_id for event_id in ids if event_id not in linked]
    )
    outcomes: Counter[str] = Counter()
    # Order of the batch, not of arrival: the same events give the same cases.
    for event in sorted(events, key=_order):
        outcomes[_resolve_event(session, event)] += 1
    return replace(
        report,
        events_seen=report.events_seen + len(ids),
        unchanged=report.unchanged + len(linked),
        reattached=report.reattached + outcomes["reattached"],
        joined=report.joined + outcomes["joined"],
        opened=report.opened + outcomes["opened"] + outcomes["opened_ambiguous"],
        ambiguous=report.ambiguous + outcomes["opened_ambiguous"],
        moved_out=report.moved_out + outcomes["moved_out"],
        without_case=report.without_case + outcomes["without_case"],
        batches=report.batches + 1,
    )


def _order(event: CaseEvent) -> tuple[bool, date, int, str, str]:
    return (
        event.event_date is None,
        event.event_date or date.max,
        event.article_id,
        event.event_type,
        event.quote,
    )


def _free_key(session: Session, event: CaseEvent) -> tuple[str, CaseEventLinkRecord | None]:
    """The event's key, and its link if it has one. A key taken by another live event (the
    same sentence twice in one article) is skipped for the next ordinal."""
    ordinal = 0
    while True:
        key = evidence_key(event, ordinal=ordinal)
        link = session.get(CaseEventLinkRecord, key)
        if link is None or link.event_id in (None, event.id):
            return key, link
        ordinal += 1


def _resolve_event(session: Session, event: CaseEvent) -> str:
    key, link = _free_key(session, event)
    evidence = evidence_of(event)
    if link is not None:
        return _reattach(session, link, event, evidence)
    if not evidence.targets:
        # Nobody is named: there is no case to form. The link keeps the fact that the
        # event was looked at, so a later run does not look at it again.
        _link_without_case(session, key, event, evidence)
        return "without_case"
    candidates = _candidates(session, evidence)
    chosen = matching.choose_case([(str(case_id), match) for case_id, match in candidates])
    recorded = [
        {
            "case_id": str(case_id),
            "score": match.score,
            "features": list(match.features),
            "vetoes": list(match.vetoes),
        }
        for case_id, match in sorted(candidates, key=lambda pair: (-pair[1].score, str(pair[0])))
        if match.score >= _RECORDED_SCORE
    ][:MAX_RECORDED_CANDIDATES]
    if chosen is not None:
        case_id = UUID(chosen)
        match = next(match for found, match in candidates if found == case_id)
        _link(session, key, case_id, event, evidence, match, recorded)
        return "joined"
    case_id = _open_case(session, event)
    _link(session, key, case_id, event, evidence, Match(0.0, ()), recorded)
    return "opened_ambiguous" if recorded else "opened"


def _link_without_case(session: Session, key: str, event: CaseEvent, evidence: Evidence) -> None:
    """The event names nobody: it is recorded as looked at, with no case."""
    session.add(
        CaseEventLinkRecord(
            evidence_key=key,
            case_id=None,
            event_id=event.id,
            article_id=event.article_id,
            active=True,
            status="candidate",
            case_number=None,
            targets=[],
            evidence=evidence.to_json(),
            score=0.0,
            reasons=[],
            candidates=[],
            algorithm_version=matching.VERSION,
        )
    )


def _candidates(session: Session, evidence: Evidence) -> list[tuple[UUID, Match]]:
    """Cases that may be this event's: those of the people it is about. Only the members
    sharing a person are loaded, so the cost follows the person, not the database."""
    if not evidence.targets:
        return []
    rows = session.execute(
        select(CaseEventLinkRecord.case_id, CaseEventLinkRecord.evidence).where(
            CaseEventLinkRecord.active.is_(True),
            CaseEventLinkRecord.targets.has_any(array(sorted(evidence.targets))),
        )
    ).all()
    members: dict[UUID, list[Evidence]] = {}
    for case_id, stored in rows:
        members.setdefault(case_id, []).append(Evidence.from_json(stored))
    return [
        (case_id, matching.score_case(evidence, found))
        for case_id, found in sorted(members.items())
    ]


def _open_case(session: Session, event: CaseEvent) -> UUID:
    names = ", ".join(person.name for person in event.targets) or "Неназванный фигурант"
    day = f", {event.event_date.isoformat()}" if event.event_date else ""
    case = CriminalCaseRecord(
        preliminary=True,
        case_number=None,
        description=f"{names}: {event.event_type}{day}",
        algorithm_version=matching.VERSION,
    )
    session.add(case)
    session.flush()
    return case.id


def _link(
    session: Session,
    key: str,
    case_id: UUID,
    event: CaseEvent,
    evidence: Evidence,
    match: Match,
    recorded: list[dict[str, Any]],
) -> None:
    session.add(
        CaseEventLinkRecord(
            evidence_key=key,
            case_id=case_id,
            event_id=event.id,
            article_id=event.article_id,
            active=True,
            status="confirmed",
            case_number=evidence.case_number,
            targets=sorted(evidence.targets),
            evidence=evidence.to_json(),
            score=match.score,
            reasons=list(match.features),
            candidates=recorded,
            algorithm_version=matching.VERSION,
        )
    )
    session.flush()
    _set_participants(session, key, event)


def _set_participants(session: Session, key: str, event: CaseEvent) -> None:
    session.execute(delete(CaseParticipantRecord).where(CaseParticipantRecord.evidence_key == key))
    session.add_all(
        CaseParticipantRecord(
            evidence_key=key,
            person_key=person.key,
            role="figurant" if person.role == "figurant" else None,
        )
        for person in event.targets
    )
    session.flush()


def _reattach(
    session: Session, link: CaseEventLinkRecord, event: CaseEvent, evidence: Evidence
) -> str:
    """A rebuilt event whose link is already there: tied back to it. When the people it
    names changed so that it no longer shares one with the rest of its case, it leaves the
    case for one of its own; a link is otherwise never moved."""
    changed = link.evidence != evidence.to_json()
    link.event_id = event.id
    link.active = True
    if not changed:
        session.flush()
        return "reattached"
    link.evidence = evidence.to_json()
    link.targets = sorted(evidence.targets)
    link.case_number = evidence.case_number
    session.flush()
    _set_participants(session, link.evidence_key, event)
    others = session.scalars(
        select(CaseEventLinkRecord.targets).where(
            CaseEventLinkRecord.case_id == link.case_id,
            CaseEventLinkRecord.active.is_(True),
            CaseEventLinkRecord.evidence_key != link.evidence_key,
        )
    ).all()
    if others and not any(set(found) & evidence.targets for found in others):
        link.case_id = _open_case(session, event)
        link.reasons = []
        link.score = 0.0
        session.flush()
        return "moved_out"
    return "reattached"


def _deactivate_orphans(session: Session, report: ResolutionReport) -> ResolutionReport:
    """After a whole pass: a link whose event was not found again (the article's words
    changed, or the event is gone) no longer stands. It stays as history, with its case."""
    result = session.execute(
        update(CaseEventLinkRecord)
        .where(CaseEventLinkRecord.event_id.is_(None), CaseEventLinkRecord.active.is_(True))
        .values(active=False)
    )
    return replace(report, deactivated=cast(CursorResult[Any], result).rowcount)
