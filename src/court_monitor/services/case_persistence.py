"""Service for persisting court cases and events."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from court_monitor.matching.case_matching import _is_decision_event
from court_monitor.observability import get_logger
from court_monitor.parsers.sud_delo import ParsedCaseCard
from court_monitor.storage.orm import Case, CourtEvent

_log = get_logger(__name__)


def persist_case_card(
    session: Session,
    parsed_card: ParsedCaseCard,
    source_url: str,
    court_name: str,
) -> tuple[Case, bool]:
    """Persist a parsed case card to the database.

    Idempotent: if case already exists (by case_uid or court+case_number),
    updates it instead of creating a duplicate.

    Args:
        session: Database session
        parsed_card: Parsed case card data
        source_url: URL where the case card was fetched from
        court_name: Name of the court (provenance)

    Returns:
        Tuple of (Case, created) where created indicates if this is a new case
    """
    # Try to find existing case by case_uid (strongest identifier)
    case: Case | None = None

    if parsed_card.case_uid:
        case = session.execute(
            select(Case).where(Case.case_uid == parsed_card.case_uid)
        ).scalar_one_or_none()

    # If not found by UID, try court + case_number
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

        _log.info(
            "case.persisted.created",
            case_id=case.id,
            case_uid=case.case_uid,
            case_number=case.case_number,
            court=case.court,
        )
    else:
        assert case is not None  # created=False implies case was found
        if parsed_card.received_at:
            case.received_at = datetime.combine(parsed_card.received_at, datetime.min.time())
        case.source_url = source_url
        case.judge = parsed_card.judge or case.judge
        case.first_instance_court = parsed_card.first_instance_court or case.first_instance_court
        case.first_instance_case_number = (
            parsed_card.first_instance_case_number or case.first_instance_case_number
        )
        case.first_instance_judge = parsed_card.first_instance_judge or case.first_instance_judge

        _log.info(
            "case.persisted.updated",
            case_id=case.id,
            case_uid=case.case_uid,
            case_number=case.case_number,
        )

    case_db: Case = case
    persist_case_events(session, case_db, parsed_card)
    _update_decision_at(session, case_db)
    session.flush()
    return case_db, created


def persist_case_events(
    session: Session,
    case: Case,
    parsed_card: ParsedCaseCard,
) -> tuple[int, int]:
    """Persist court events for a case.

    Idempotent: uses event fingerprint to avoid duplicates.

    Args:
        session: Database session
        case: Case to attach events to
        parsed_card: Parsed case card with events

    Returns:
        Tuple of (created_count, skipped_count)
    """
    created_count = 0
    skipped_count = 0

    for event in parsed_card.events:
        # Check if event already exists (using composite key with NULL-safe comparison)
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

        if existing:
            skipped_count += 1
            continue

        # Create new event
        court_event = CourtEvent(
            case_id=case.id,
            event_type=event.event_type,
            event_date=event.event_date,
            event_time=event.event_time,
            result=event.result,
            location=event.location,
        )
        session.add(court_event)
        created_count += 1

    if created_count > 0:
        _log.info(
            "case.events.persisted",
            case_id=case.id,
            created=created_count,
            skipped=skipped_count,
        )

    return created_count, skipped_count


def _update_decision_at(session: Session, case: Case) -> None:
    """Update case.decision_at from events.

    Looks for events classified as decisions (sentence, decision, etc.)
    and sets decision_at to the earliest such event date.
    """
    # Find all decision events for this case
    decision_events = (
        session.execute(
            select(CourtEvent).where(
                CourtEvent.case_id == case.id,
            )
        )
        .scalars()
        .all()
    )

    # Filter to decision events
    decision_dates = [
        e.event_date for e in decision_events if e.event_date and _is_decision_event(e.event_type)
    ]

    if decision_dates:
        # Use earliest decision date
        case.decision_at = min(decision_dates)
    else:
        # No decision events found
        case.decision_at = None
