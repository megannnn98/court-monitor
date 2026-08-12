"""Integration test: full pipeline from press release to case matching.

Tests the complete flow:
1. Ingest press release from fixture
2. Extract facts (name, article, date)
3. Create test case card in DB
4. Search for matching cases
5. Verify explainable matching results
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from sqlalchemy.orm import Session

from court_monitor.config.loader import SourceConfig, load_monitoring
from court_monitor.domain.models import SourceBackend, SourceType
from court_monitor.matching.case_matching import match_press_release_to_case
from court_monitor.parsers.sud_delo import CasePerson, ParsedCaseCard
from court_monitor.services import process_source
from court_monitor.services.case_search import CaseSearchCriteria, search_cases
from court_monitor.storage import repository as repo
from court_monitor.storage.orm import Case, PersonCase, PersonRecord

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "sudrf-live" / "2zovs" / "press"


def _make_config() -> SourceConfig:
    return SourceConfig(
        name="2zovs-test",
        type=SourceType.sudrf,
        backend=SourceBackend.fixture,
        base_url="https://2zovs.msk.sudrf.ru",
        fixture_path=str(FIXTURE_DIR),
        court_name="2-й Западный окружной военный суд",
        press_module="press_dep",
    )


def _create_test_person(session: Session, name: str) -> PersonRecord:
    """Helper to create a test person in DB."""
    person = PersonRecord(
        source="test",
        raw_name=name,
        search_name=name.lower(),
        normalized_name=name,
        normalization_method="manual",
        normalization_confidence=1.0,
    )
    session.add(person)
    session.flush()
    return person


def _create_test_case(
    session: Session,
    *,
    case_number: str,
    court: str,
    received_at: date,
    person_name: str,
    articles: list[str],
) -> Case:
    """Helper to create a test case in DB."""
    # Create person first
    person = _create_test_person(session, person_name)

    case = Case(
        court=court,
        case_number=case_number,
        case_uid=f"test-uid-{case_number}",
        received_at=datetime.combine(received_at, datetime.min.time()),
        status="active",
    )
    session.add(case)
    session.flush()

    # Add person
    person_case = PersonCase(
        person_id=person.id,
        case_id=case.id,
        role="defendant",
        articles="; ".join(articles),
    )
    session.add(person_case)
    session.flush()

    return case


def test_press_release_extraction(db_session: Session) -> None:
    """Test that press releases are correctly ingested and facts extracted."""
    config = _make_config()
    monitoring = load_monitoring()

    # Ingest fixtures
    stats = process_source(db_session, config, monitoring, parse_immediately=True)

    assert stats.new_documents >= 4
    assert stats.parsed >= 1

    # Find the Razlugo document (did=234)
    docs = repo.list_documents(db_session, limit=100)
    razlugo_doc = next((d for d in docs if d.external_id == "234"), None)

    assert razlugo_doc is not None
    assert razlugo_doc.title is not None
    assert "Брянск" in razlugo_doc.title or "финансирование" in razlugo_doc.title

    # Check extracted facts
    assert len(razlugo_doc.facts) > 0
    fact_fields = {f.field for f in razlugo_doc.facts}

    # Should have extracted article
    assert "criminal_article" in fact_fields

    # Find article fact
    article_fact = next(f for f in razlugo_doc.facts if f.field == "criminal_article")
    assert article_fact.value is not None


def test_case_search_by_article(db_session: Session) -> None:
    """Test searching cases by article."""
    # Create test cases
    case1 = _create_test_case(
        db_session,
        case_number="1-100/2026",
        court="2-й Западный окружной военный суд",
        received_at=date(2026, 4, 2),
        person_name="Иванов Иван Иванович",
        articles=["ст.205.1 ч.1 УК РФ"],
    )

    _create_test_case(
        db_session,
        case_number="1-101/2026",
        court="2-й Западный окружной военный суд",
        received_at=date(2026, 4, 3),
        person_name="Петров Петр Петрович",
        articles=["ст.105 ч.1 УК РФ"],
    )

    db_session.commit()

    # Search for article 205.1
    criteria = CaseSearchCriteria(article="205.1")
    candidates = search_cases(db_session, criteria)

    # Should find case1 but not case2
    assert len(candidates) >= 1
    case_numbers = [c.case.case_number for c in candidates]
    assert case1.case_number in case_numbers


def test_case_search_by_date(db_session: Session) -> None:
    """Test searching cases by date with tolerance."""
    # Create test case
    case = _create_test_case(
        db_session,
        case_number="1-200/2026",
        court="2-й Западный окружной военный суд",
        received_at=date(2026, 4, 2),
        person_name="Сидоров Сидор Сидорович",
        articles=["ст.205 УК РФ"],
    )

    db_session.commit()

    # Search for exact date
    criteria = CaseSearchCriteria(decision_date=date(2026, 4, 2))
    candidates = search_cases(db_session, criteria)

    assert len(candidates) >= 1
    assert any(c.case.case_number == case.case_number for c in candidates)

    # Search for date within tolerance (3 days off)
    criteria = CaseSearchCriteria(decision_date=date(2026, 4, 5))
    candidates = search_cases(db_session, criteria)

    assert len(candidates) >= 1
    assert any(c.case.case_number == case.case_number for c in candidates)


def test_case_search_by_person_name(db_session: Session) -> None:
    """Test searching cases by person name."""
    # Create test cases
    case1 = _create_test_case(
        db_session,
        case_number="1-300/2026",
        court="2-й Западный окружной военный суд",
        received_at=date(2026, 4, 2),
        person_name="Разлуго Виталий Викторович",
        articles=["ст.205.1 ч.1 УК РФ"],
    )

    _create_test_case(
        db_session,
        case_number="1-301/2026",
        court="2-й Западный окружной военный суд",
        received_at=date(2026, 4, 3),
        person_name="Петров Петр Петрович",
        articles=["ст.105 ч.1 УК РФ"],
    )

    db_session.commit()

    # Search for person name
    criteria = CaseSearchCriteria(person_name="Разлуго Виталий")
    candidates = search_cases(db_session, criteria)

    # Should find case1
    assert len(candidates) >= 1
    case_numbers = [c.case.case_number for c in candidates]
    assert case1.case_number in case_numbers

    # Check that person_name match is in reasons
    for candidate in candidates:
        if candidate.case.case_number == case1.case_number:
            reason_fields = [r.field_name for r in candidate.reasons]
            assert "person_name" in reason_fields


def test_end_to_end_matching() -> None:
    """Test complete matching flow with parsed case card."""
    # Simulate extracted data from press release
    article = "205.1"
    decision_date = date(2026, 4, 2)
    person_name = "Разлуго Виталий Викторович"

    # Create parsed case card (simulating sud_delo data)
    case_card = ParsedCaseCard(
        case_number="1-456/2026",
        case_uid="real-uid-456",
        received_at=date(2026, 4, 2),
        persons=[
            CasePerson(
                name="Разлуго Виталий Викторович",
                articles=["ст.205.1 ч.1 УК РФ"],
                material="обвинительный приговор",
            )
        ],
    )

    # Match
    result = match_press_release_to_case(
        article=article,
        decision_date=decision_date,
        court=None,
        person_name=person_name,
        case_card=case_card,
    )

    # Verify results
    assert result.confidence > 0.5
    assert len(result.signals) >= 3  # article, date, person_name

    signal_types = {s.signal_type for s in result.signals}
    assert "article" in signal_types
    assert "date" in signal_types
    assert "person_name" in signal_types

    # No conflicts
    assert len(result.conflicts) == 0


def test_end_to_end_matching_hidden_person() -> None:
    """Test matching when person name is hidden in case card."""
    # Simulate extracted data from press release
    article = "205.1"
    decision_date = date(2026, 4, 2)
    person_name = "Разлуго Виталий Викторович"

    # Create parsed case card with hidden person
    case_card = ParsedCaseCard(
        case_number="1-789/2026",
        case_uid="real-uid-789",
        received_at=date(2026, 4, 2),
        persons=[
            CasePerson(
                name="Информация скрыта",
                articles=["ст.205.1 ч.1 УК РФ"],
            )
        ],
    )

    # Match
    result = match_press_release_to_case(
        article=article,
        decision_date=decision_date,
        court=None,
        person_name=person_name,
        case_card=case_card,
    )

    # Should still match by article and date
    assert result.confidence > 0.3
    assert len(result.signals) >= 2  # article, date

    # Person name should be in missing
    assert len(result.missing) >= 1
    missing_fields = {m.field_name for m in result.missing}
    assert "person_name" in missing_fields


def test_end_to_end_no_match() -> None:
    """Test matching when there's no match."""
    # Simulate extracted data from press release
    article = "205.1"
    decision_date = date(2026, 4, 2)
    person_name = "Разлуго Виталий Викторович"

    # Create parsed case card with different data
    case_card = ParsedCaseCard(
        case_number="1-999/2026",
        case_uid="real-uid-999",
        received_at=date(2026, 1, 1),  # Different date
        persons=[
            CasePerson(
                name="Петров Петр Петрович",  # Different person
                articles=["ст.105 ч.1 УК РФ"],  # Different article
            )
        ],
    )

    # Match
    result = match_press_release_to_case(
        article=article,
        decision_date=decision_date,
        court=None,
        person_name=person_name,
        case_card=case_card,
    )

    # Should not match
    assert result.confidence == 0
    assert len(result.signals) == 0
    assert len(result.missing) > 0
