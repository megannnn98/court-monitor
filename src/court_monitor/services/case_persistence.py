"""Service for persisting court cases, participants, and events."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from court_monitor.extraction.event_classifier import CaseEventType, classify_case_event
from court_monitor.normalization import normalize_fio
from court_monitor.observability import get_logger
from court_monitor.parsers.sud_delo import CaseEvent, CasePerson, ParsedCaseCard
from court_monitor.storage.orm import Case, CaseParticipant, CourtEvent, SourceDocument

_log = get_logger(__name__)


def persist_case_card(
    session: Session,
    parsed_card: ParsedCaseCard,
    source_url: str,
    court_name: str,
    source_document: SourceDocument,
) -> tuple[Case, bool]:
    """Persist a parsed case card to the database.

    Also saves the case card as a SourceDocument for provenance/reprocessing.
    Idempotent by case_uid or court+case_number.

    Returns (Case, created).
    """
    case: Case | None = None

    if parsed_card.case_uid:
        case = session.execute(
            select(Case).where(Case.case_uid == parsed_card.case_uid)
        ).scalar_one_or_none()

    if case is None and parsed_card.case_number:
        case = session.execute(
            select(Case).where(
                Case.court == court_name,
                Case.case_number == parsed_card.case_number,
            )
        ).scalar_one_or_none()

    created = case is None

    if created:
        case = Case(
            court=court_name,
            case_number=parsed_card.case_number or "",
            case_uid=parsed_card.case_uid,
            received_at=parsed_card.received_at,
            decision_at=None,
            source_url=source_url,
            judge=parsed_card.judge,
            first_instance_court=parsed_card.first_instance_court,
            first_instance_case_number=parsed_card.first_instance_case_number,
            first_instance_judge=parsed_card.first_instance_judge,
            status="active",
        )
        session.add(case)
        session.flush()
        _log.info("case.persisted.created", case_id=case.id, case_uid=case.case_uid)
    else:
        assert case is not None
        if parsed_card.received_at:
            case.received_at = datetime.combine(parsed_card.received_at, datetime.min.time())
        case.source_url = source_url
        case.judge = parsed_card.judge or case.judge
        case.first_instance_court = parsed_card.first_instance_court or case.first_instance_court
        case.first_instance_case_number = (
            parsed_card.first_instance_case_number or case.first_instance_case_number
        )
        case.first_instance_judge = parsed_card.first_instance_judge or case.first_instance_judge
        _log.info("case.persisted.updated", case_id=case.id)

    case_db: Case = case
    persist_case_events(session, case_db, parsed_card, source_document)
    persist_case_participants(session, case_db, parsed_card, source_document)
    _update_decision_at(session, case_db)
    session.flush()
    return case_db, created


def persist_case_events(
    session: Session,
    case: Case,
    parsed_card: ParsedCaseCard,
    source_document: SourceDocument | None = None,
) -> tuple[int, int, int]:
    """Persist court events. Idempotent with mutable field updates."""
    created_count = 0
    updated_count = 0
    skipped_count = 0

    for event in parsed_card.events:
        dedup_conditions: list = [
            CourtEvent.case_id == case.id,
            CourtEvent.event_type == event.event_type,
        ]
        if event.event_date is not None:
            dedup_conditions.append(CourtEvent.event_date == event.event_date)
        else:
            dedup_conditions.append(CourtEvent.event_date.is_(None))
        if event.event_time is not None:
            dedup_conditions.append(CourtEvent.event_time == event.event_time)
        else:
            dedup_conditions.append(CourtEvent.event_time.is_(None))

        existing = session.execute(
            select(CourtEvent).where(and_(*dedup_conditions))
        ).scalar_one_or_none()

        if existing is not None:
            if _update_mutable_fields(existing, event, source_document):
                updated_count += 1
            else:
                skipped_count += 1
            continue

        ce = CourtEvent(
            case_id=case.id,
            event_type=event.event_type,
            event_date=event.event_date,
            event_time=event.event_time,
            result=event.result,
            location=event.location,
            source_document_id=source_document.id if source_document else None,
        )
        session.add(ce)
        created_count += 1

    if created_count or updated_count:
        _log.info(
            "case.events.persisted",
            case_id=case.id,
            created=created_count,
            updated=updated_count,
            skipped=skipped_count,
        )
    return created_count, updated_count, skipped_count


def _update_mutable_fields(
    existing: CourtEvent,
    event: CaseEvent,
    source_document: SourceDocument | None,
) -> bool:
    """Update mutable fields if they changed. Returns True if anything changed."""
    changed = False
    if event.result is not None and existing.result != event.result:
        existing.result = event.result
        changed = True
    if event.location is not None and existing.location != event.location:
        existing.location = event.location
        changed = True
    if source_document and existing.source_document_id != source_document.id:
        existing.source_document_id = source_document.id
        changed = True
    if changed:
        existing.updated_at = datetime.now(UTC)
    return changed


def persist_case_participants(
    session: Session,
    case: Case,
    parsed_card: ParsedCaseCard,
    source_document: SourceDocument | None = None,
) -> tuple[int, int]:
    """Persist case participants. Idempotent by case_id + name_original."""
    created_count = 0
    skipped_count = 0

    for person in parsed_card.persons:
        existing = session.execute(
            select(CaseParticipant).where(
                CaseParticipant.case_id == case.id,
                CaseParticipant.name_original == person.name,
            )
        ).scalar_one_or_none()

        if existing is not None:
            _update_participant_mutable(existing, person, source_document)
            skipped_count += 1
            continue

        cp = CaseParticipant(
            case_id=case.id,
            name_original=person.name,
            normalized_name=_normalize_person_name(person.name),
            articles="; ".join(person.articles) if person.articles else None,
            material=person.material,
            result=person.result,
            is_hidden=_is_hidden_name(person.name),
            source_document_id=source_document.id if source_document else None,
        )
        session.add(cp)
        created_count += 1

    if created_count:
        _log.info(
            "case.participants.persisted",
            case_id=case.id,
            created=created_count,
            skipped=skipped_count,
        )
    return created_count, skipped_count


def _update_participant_mutable(
    existing: CaseParticipant,
    person: CasePerson,
    source_document: SourceDocument | None,
) -> None:
    if person.articles:
        existing.articles = "; ".join(person.articles)
    if person.material:
        existing.material = person.material
    if person.result:
        existing.result = person.result
    if source_document and existing.source_document_id != source_document.id:
        existing.source_document_id = source_document.id
    existing.updated_at = datetime.now(UTC)


_HIDDEN_PATTERNS = (
    "информация скрыта",
    "данные скрыты",
    "сведения скрыты",
    "информация отсутствует",
    "данные отсутствуют",
)


def _is_hidden_name(name: str) -> bool:
    return any(p in name.lower() for p in _HIDDEN_PATTERNS)


def _normalize_person_name(name: str) -> str:
    return normalize_fio(name) or " ".join(name.lower().split())


def _update_decision_at(session: Session, case: Case) -> None:
    """Update case.decision_at from decision-classified events."""
    decision_events = (
        session.execute(select(CourtEvent).where(CourtEvent.case_id == case.id)).scalars().all()
    )

    decision_dates = []
    for e in decision_events:
        if e.event_date is None:
            continue
        classification = classify_case_event(e.event_type, e.result)
        if classification.event_type in (CaseEventType.sentence_delivered,):
            decision_dates.append(e.event_date)

    case.decision_at = min(decision_dates) if decision_dates else None
