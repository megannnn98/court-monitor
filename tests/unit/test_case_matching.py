"""Tests for case search and matching."""

from __future__ import annotations

from datetime import date

from court_monitor.matching.case_matching import format_match_result, match_press_release_to_case
from court_monitor.parsers.sud_delo import CaseEvent, CasePerson, ParsedCaseCard


def test_match_by_article() -> None:
    """Test matching by article."""
    case_card = ParsedCaseCard(
        case_number="1-123/2026",
        case_uid="test-uid-1",
        persons=[
            CasePerson(
                name="Иванов Иван Иванович",
                articles=["ст.205.1 ч.1 УК РФ"],
            )
        ],
    )

    result = match_press_release_to_case(
        article="205.1",
        decision_date=None,
        court=None,
        person_name=None,
        case_card=case_card,
    )

    assert result.confidence > 0
    assert len(result.signals) == 1
    assert result.signals[0].signal_type == "article"
    assert "205.1" in result.signals[0].description


def test_match_by_date() -> None:
    """Test matching by decision date from events."""

    case_card = ParsedCaseCard(
        case_number="1-123/2026",
        case_uid="test-uid-2",
        events=[
            CaseEvent(
                event_type="Приговор",
                event_date=date(2026, 4, 2),
            )
        ],
        persons=[],
    )

    result = match_press_release_to_case(
        article=None,
        decision_date=date(2026, 4, 2),
        court=None,
        person_name=None,
        case_card=case_card,
    )

    assert result.confidence > 0
    assert len(result.signals) == 1
    assert result.signals[0].signal_type == "date"


def test_match_by_date_with_tolerance() -> None:
    """Test that date matching works within tolerance."""

    case_card = ParsedCaseCard(
        case_number="1-123/2026",
        case_uid="test-uid-3",
        events=[
            CaseEvent(
                event_type="Приговор",
                event_date=date(2026, 4, 5),  # 3 days after
            )
        ],
        persons=[],
    )

    result = match_press_release_to_case(
        article=None,
        decision_date=date(2026, 4, 2),
        court=None,
        person_name=None,
        case_card=case_card,
    )

    assert result.confidence > 0
    assert len(result.signals) == 1
    assert result.signals[0].signal_type == "date"


def test_match_decision_date_not_received() -> None:
    """Test that decision_date does NOT match received_at.

    Regression test: decision_date should only match sentence/decision events,
    not case receipt date.
    """
    case_card = ParsedCaseCard(
        case_number="1-123/2026",
        case_uid="test-uid-date-strict",
        received_at=date(2026, 4, 2),  # Only received_at, no decision events
        persons=[],
    )

    result = match_press_release_to_case(
        article=None,
        decision_date=date(2026, 4, 2),
        court=None,
        person_name=None,
        case_card=case_card,
    )

    # Should NOT match because received_at is not a decision event
    assert result.confidence == 0
    assert len(result.signals) == 0
    assert len(result.missing) == 1
    assert result.missing[0].field_name == "date"


def test_match_by_person_name() -> None:
    """Test matching by person name."""
    case_card = ParsedCaseCard(
        case_number="1-123/2026",
        case_uid="test-uid-4",
        persons=[
            CasePerson(
                name="Разлуго Виталий Викторович",
                articles=["ст.205.1 УК РФ"],
            )
        ],
    )

    result = match_press_release_to_case(
        article=None,
        decision_date=None,
        court=None,
        person_name="Разлуго Виталий",
        case_card=case_card,
    )

    assert result.confidence > 0
    assert len(result.signals) == 1
    assert result.signals[0].signal_type == "person_name"


def test_match_combined_signals() -> None:
    """Test matching with multiple signals."""

    case_card = ParsedCaseCard(
        case_number="1-123/2026",
        case_uid="test-uid-5",
        events=[
            CaseEvent(
                event_type="Приговор",
                event_date=date(2026, 4, 2),
            )
        ],
        persons=[
            CasePerson(
                name="Разлуго Виталий Викторович",
                articles=["ст.205.1 ч.1 УК РФ"],
            )
        ],
    )

    result = match_press_release_to_case(
        article="205.1",
        decision_date=date(2026, 4, 2),
        court=None,
        person_name="Разлуго Виталий",
        case_card=case_card,
    )

    assert result.confidence > 0.5
    assert len(result.signals) == 3
    signal_types = {s.signal_type for s in result.signals}
    assert signal_types == {"article", "date", "person_name"}


def test_match_hidden_person() -> None:
    """Test matching when person name is hidden in case card."""

    case_card = ParsedCaseCard(
        case_number="1-123/2026",
        case_uid="test-uid-6",
        events=[
            CaseEvent(
                event_type="Приговор",
                event_date=date(2026, 4, 2),
            )
        ],
        persons=[
            CasePerson(
                name="Информация скрыта",
                articles=["ст.205.1 ч.1 УК РФ"],
            )
        ],
    )

    result = match_press_release_to_case(
        article="205.1",
        decision_date=date(2026, 4, 2),
        court=None,
        person_name="Разлуго Виталий",
        case_card=case_card,
    )

    # Should match by article and date, but person name should be in missing
    assert result.confidence > 0
    assert len(result.signals) == 2
    assert len(result.missing) == 1
    assert result.missing[0].field_name == "person_name"
    assert "скрыто" in result.missing[0].description.lower()


def test_match_hidden_person_variations() -> None:
    """Test matching with different hidden person patterns."""

    for hidden_name in ["Информация скрыта", "Данные скрыты", "Сведения скрыты"]:
        case_card = ParsedCaseCard(
            case_number="1-123/2026",
            case_uid="test-uid-6",
            events=[
                CaseEvent(
                    event_type="Приговор",
                    event_date=date(2026, 4, 2),
                )
            ],
            persons=[
                CasePerson(
                    name=hidden_name,
                    articles=["ст.205.1 ч.1 УК РФ"],
                )
            ],
        )

        result = match_press_release_to_case(
            article="205.1",
            decision_date=date(2026, 4, 2),
            court=None,
            person_name="Разлуго Виталий",
            case_card=case_card,
        )

        # Should match by article and date, but person name should be in missing
        assert result.confidence > 0
        assert len(result.missing) == 1
        assert result.missing[0].field_name == "person_name"


def test_match_no_match() -> None:
    """Test when nothing matches."""
    case_card = ParsedCaseCard(
        case_number="1-999/2026",
        case_uid="test-uid-7",
        received_at=date(2026, 1, 1),
        persons=[
            CasePerson(
                name="Петров Петр Петрович",
                articles=["ст.105 ч.1 УК РФ"],
            )
        ],
    )

    result = match_press_release_to_case(
        article="205.1",
        decision_date=date(2026, 4, 2),
        court=None,
        person_name="Разлуго Виталий",
        case_card=case_card,
    )

    assert result.confidence == 0
    assert len(result.signals) == 0
    assert len(result.missing) > 0


def test_match_article_normalization() -> None:
    """Test that article matching handles different formats."""
    case_card = ParsedCaseCard(
        case_number="1-123/2026",
        case_uid="test-uid-8",
        persons=[
            CasePerson(
                name="Иванов Иван Иванович",
                articles=["ст.205.1 ч.1 УК РФ"],
            )
        ],
    )

    # Test different article formats
    for article in ["205.1", "ст.205.1", "205.1 УК РФ", "ст. 205.1 ч.1"]:
        result = match_press_release_to_case(
            article=article,
            decision_date=None,
            court=None,
            person_name=None,
            case_card=case_card,
        )
        assert result.confidence > 0, f"Failed for article format: {article}"


def test_match_article_exact_match() -> None:
    """Test that article matching is exact, not substring."""
    case_card = ParsedCaseCard(
        case_number="1-123/2026",
        case_uid="test-uid-10",
        persons=[
            CasePerson(
                name="Иванов Иван Иванович",
                articles=["ст.205.1 ч.1 УК РФ"],
            )
        ],
    )

    # "205" should NOT match "205.1"
    result = match_press_release_to_case(
        article="205",
        decision_date=None,
        court=None,
        person_name=None,
        case_card=case_card,
    )
    assert result.confidence == 0, "Article '205' should not match '205.1'"

    # "205.1" should match "205.1"
    result = match_press_release_to_case(
        article="205.1",
        decision_date=None,
        court=None,
        person_name=None,
        case_card=case_card,
    )
    assert result.confidence > 0, "Article '205.1' should match '205.1'"


def test_match_result_format() -> None:
    """Test that match result can be formatted for display."""

    case_card = ParsedCaseCard(
        case_number="1-123/2026",
        case_uid="test-uid-9",
        events=[
            CaseEvent(
                event_type="Приговор",
                event_date=date(2026, 4, 2),
            )
        ],
        persons=[
            CasePerson(
                name="Разлуго Виталий Викторович",
                articles=["ст.205.1 ч.1 УК РФ"],
            )
        ],
    )

    result = match_press_release_to_case(
        article="205.1",
        decision_date=date(2026, 4, 2),
        court=None,
        person_name="Разлуго Виталий",
        case_card=case_card,
    )

    formatted = format_match_result(result)
    assert "case_number" in formatted
    assert "confidence" in formatted
    assert "signals" in formatted
    assert "missing" in formatted
    assert len(formatted["signals"]) == 3
