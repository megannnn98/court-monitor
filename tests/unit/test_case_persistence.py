"""Tests for case persistence service."""

from __future__ import annotations

from datetime import date

from court_monitor.parsers.sud_delo import CaseEvent, CasePerson, ParsedCaseCard
from court_monitor.services.case_persistence import persist_case_card


def _make_parsed_card(
    *,
    case_uid: str = "test-uid-001",
    case_number: str = "1-100/2026",
    received_at: date | None = date(2026, 4, 1),
    events: list[CaseEvent] | None = None,
) -> ParsedCaseCard:
    return ParsedCaseCard(
        case_number=case_number,
        case_uid=case_uid,
        court="2-й Западный окружной военный суд",
        received_at=received_at,
        judge="Иванов И.И.",
        first_instance_court="Гарнизонный суд",
        first_instance_case_number="3-1/2026",
        first_instance_judge="Петров П.П.",
        persons=[
            CasePerson(name="Тестов Тест Тестович", articles=["ст.205.1 ч.1 УК РФ"]),
        ],
        events=events or [],
    )


def test_persist_case_card_creates(db_session):
    """Test that persist_case_card creates a new Case."""
    card = _make_parsed_card()

    case, created = persist_case_card(
        db_session, card, "https://court.local/card", "2-й Западный окружной военный суд"
    )
    db_session.commit()

    assert created is True
    assert case.id is not None
    assert case.case_uid == "test-uid-001"
    assert case.case_number == "1-100/2026"
    assert case.court == "2-й Западный окружной военный суд"
    assert case.judge == "Иванов И.И."
    assert case.first_instance_court == "Гарнизонный суд"
    assert case.received_at is not None
    assert case.received_at.year == 2026


def test_persist_case_card_idempotent_by_uid(db_session):
    """Test that persist_case_card finds existing case by case_uid."""
    card = _make_parsed_card()

    case1, created1 = persist_case_card(
        db_session, card, "url1", "2-й Западный окружной военный суд"
    )
    db_session.commit()
    assert created1 is True

    # Second call with same case_uid
    card2 = _make_parsed_card(case_uid="test-uid-001", case_number="1-200/2026")
    case2, created2 = persist_case_card(
        db_session, card2, "url2", "2-й Западный окружной военный суд"
    )
    db_session.commit()

    assert created2 is False
    assert case1.id == case2.id
    assert case2.source_url == "url2"  # Updated
    assert case2.case_number == "1-100/2026"  # Not overwritten by different number


def test_persist_case_card_idempotent_by_number(db_session):
    """Test that persist_case_card finds existing case by court+case_number."""
    card1 = _make_parsed_card(case_uid=None)  # No UID

    case1, created1 = persist_case_card(
        db_session, card1, "url1", "2-й Западный окружной военный суд"
    )
    db_session.commit()
    assert created1 is True

    # Second call with same court+case_number but different UID
    card2 = _make_parsed_card(case_uid="new-uid-later", case_number="1-100/2026")
    case2, created2 = persist_case_card(
        db_session, card2, "url2", "2-й Западный окружной военный суд"
    )
    db_session.commit()

    assert created2 is False
    assert case1.id == case2.id


def test_persist_case_events(db_session):
    """Test that court events are persisted."""
    card = _make_parsed_card(
        events=[
            CaseEvent(
                event_type="Судебное заседание", event_date=date(2026, 4, 5), event_time="10:00"
            ),
            CaseEvent(event_type="Приговор", event_date=date(2026, 4, 10)),
        ],
    )

    case, created = persist_case_card(db_session, card, "url", "2-й Западный окружной военный суд")
    db_session.commit()

    assert len(case.events) == 2
    event_types = {e.event_type for e in case.events}
    assert "Судебное заседание" in event_types
    assert "Приговор" in event_types

    # decision_at should be set from the decision event
    assert case.decision_at is not None
    assert case.decision_at.year == 2026
    assert case.decision_at.month == 4
    assert case.decision_at.day == 10


def test_persist_case_events_idempotent(db_session):
    """Test that re-persisting same events does not create duplicates."""
    card = _make_parsed_card(
        events=[
            CaseEvent(event_type="Приговор", event_date=date(2026, 4, 10), event_time="10:00"),
        ],
    )

    case1, _ = persist_case_card(db_session, card, "url", "2-й Западный окружной военный суд")
    db_session.commit()
    assert len(case1.events) == 1

    # Second persist with same events
    case2, _ = persist_case_card(db_session, card, "url2", "2-й Западный окружной военный суд")
    db_session.commit()

    assert case2.id == case1.id
    assert len(case2.events) == 1  # No duplicates


def test_persist_case_events_null_safe_dedup(db_session):
    """Test that events with NULL date/time are properly deduplicated."""
    card = _make_parsed_card(
        events=[
            CaseEvent(event_type="Передача дела судье", event_date=None, event_time=None),
        ],
    )

    case1, _ = persist_case_card(db_session, card, "url", "2-й Западный окружной военный суд")
    db_session.commit()
    assert len(case1.events) == 1

    # Second persist with same NULL-field event
    case2, _ = persist_case_card(db_session, card, "url2", "2-й Западный окружной военный суд")
    db_session.commit()

    assert case2.id == case1.id
    assert len(case2.events) == 1  # NULL=NULL dedup works


def test_persist_case_card_no_decision_events(db_session):
    """Test that decision_at remains None if no decision events exist."""
    card = _make_parsed_card(
        events=[
            CaseEvent(event_type="Судебное заседание", event_date=date(2026, 4, 5)),
            CaseEvent(event_type="Передача дела судье", event_date=date(2026, 4, 1)),
        ],
    )

    case, _ = persist_case_card(db_session, card, "url", "2-й Западный окружной военный суд")
    db_session.commit()

    assert case.decision_at is None
