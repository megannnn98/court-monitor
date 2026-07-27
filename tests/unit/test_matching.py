"""Tests: person matching — name normalization, scoring, candidate generation."""

from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from court_monitor.domain.models import VerificationStatus
from court_monitor.matching.candidates import generate_matches
from court_monitor.matching.name_normalizer import normalize_name
from court_monitor.matching.score import CANDIDATE_THRESHOLD, score_match
from court_monitor.services import import_rfm_records
from court_monitor.sources.fedsfm import load_fixture_rows
from court_monitor.storage import repository as repo
from court_monitor.storage.orm import Base, ExtractedFact, PersonRecord, SourceDocument


@pytest.fixture()
def db_session() -> Session:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session, future=True)
    session = factory()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


# --- Name normalization tests ---


def test_full_name_nominative():
    n = normalize_name("Иванов Иван Иванович")
    assert n.surname == "иванов"
    assert n.name == "иван"
    assert n.patronymic == "иванович"
    assert n.initials == "иии"
    assert n.confidence >= 0.8


def test_full_name_oblique_case():
    n = normalize_name("Иванова Ивана Ивановича")
    assert n.surname == "иванова"
    assert n.name == "ивана"  # name not trimmed (no morphological analysis)
    assert n.patronymic == "иванович"  # trimmed from "ивановича"
    assert n.confidence >= 0.8


def test_initials():
    n = normalize_name("Иванов И. И.")
    assert n.surname == "иванов"
    assert n.name == "и."
    assert n.patronymic == "и."
    assert n.initials == "иии"


def test_yo_e_normalization():
    n = normalize_name("Семёнов Пётр Алексеевич")
    assert n.surname == "семенов"
    assert n.name == "петр"


def test_hyphenated_surname():
    n = normalize_name("Иванов-Петров Иван Иванович")
    # normalize_fio splits hyphens: "иванов петров иван иванович"
    assert n.surname == "иванов"
    assert n.name == "петров"


def test_two_token_name():
    n = normalize_name("Иванов Иван")
    assert n.surname == "иванов"
    assert n.name == "иван"
    assert n.patronymic == ""
    assert n.confidence < 0.8


def test_empty_name():
    n = normalize_name("")
    assert n.surname == ""
    assert n.confidence == 0.0


# --- Scoring tests ---


def test_full_name_match_high_score():
    doc = normalize_name("Иванов Иван Иванович")
    rec = normalize_name("Иванов Иван Иванович")
    result = score_match(doc, rec, "1980-01-01", "1980-01-01")
    assert result.score >= 0.8


def test_oblique_case_no_match():
    """Oblique case surnames don't match without morphological analysis."""
    doc = normalize_name("Иванова Ивана Ивановича")
    rec = normalize_name("Иванов Иван Иванович")
    result = score_match(doc, rec, None, None)
    # "иванова" != "иванов" — known limitation
    assert result.score == 0.0


def test_only_surname_low_score():
    doc = normalize_name("Иванов")
    rec = normalize_name("Иванов Иван Иванович")
    result = score_match(doc, rec, None, None)
    assert result.score < CANDIDATE_THRESHOLD


def test_birth_date_match():
    doc = normalize_name("Иванов Иван Иванович")
    rec = normalize_name("Иванов Иван Иванович")
    result = score_match(doc, rec, "1980-01-01", "1980-01-01")
    assert result.birth_date_score > 0
    assert any(r["rule"] == "birth_date_match" for r in result.reasons)


def test_birth_date_conflict():
    doc = normalize_name("Иванов Иван Иванович")
    rec = normalize_name("Иванов Иван Иванович")
    result = score_match(doc, rec, "1980-01-01", "1990-05-15")
    assert result.birth_date_score < 0
    assert any(c["rule"] == "birth_date_conflict" for c in result.conflicts)


def test_missing_birth_date_not_conflict():
    doc = normalize_name("Иванов Иван Иванович")
    rec = normalize_name("Иванов Иван Иванович")
    result = score_match(doc, rec, None, "1980-01-01")
    assert result.birth_date_score == 0.0
    assert not any(c["rule"] == "birth_date_conflict" for c in result.conflicts)


def test_different_surname_zero_score():
    doc = normalize_name("Иванов Иван Иванович")
    rec = normalize_name("Петров Иван Иванович")
    result = score_match(doc, rec, None, None)
    assert result.score == 0.0


def test_yo_e_in_scoring():
    doc = normalize_name("Семёнов Пётр")
    rec = normalize_name("Семенов Петр")
    result = score_match(doc, rec, None, None)
    assert result.score > 0


def test_score_clamped():
    doc = normalize_name("Иванов Иван Иванович")
    rec = normalize_name("Иванов Иван Иванович")
    result = score_match(doc, rec, "1980-01-01", "1980-01-01", doc_birth_place="г. Москва", record_birth_place="г. Москва")
    assert 0.0 <= result.score <= 1.0


# --- Candidate generation tests ---


def _make_doc_with_person_fact(session: Session, name: str) -> None:
    """Create a document with a person fact for testing."""
    doc = SourceDocument(
        url="https://test/1",
        source_type="sudrf",
        content_hash="test_hash",
        parser_status="parsed",
        content="<html>test</html>",
    )
    session.add(doc)
    session.flush()

    fact = ExtractedFact(
        document_id=doc.id,
        entity="person",
        field="full_name_original",
        value=name,
        verification_status=VerificationStatus.inferred.value,
        confidence=0.95,
        quote=f"...{name}...",
        extraction_method="regex:name:full_fio",
    )
    session.add(fact)
    session.flush()


def test_generate_matches_creates_candidates(db_session):
    # Import RFM records
    rows = load_fixture_rows()
    import_rfm_records(db_session, rows)

    # Create a document with matching person
    _make_doc_with_person_fact(db_session, "Иванов Иван Иванович")

    stats = generate_matches(db_session)
    assert stats["candidates_created"] >= 1
    assert stats["facts_person"] == 1


def test_generate_matches_no_duplicate(db_session):
    rows = load_fixture_rows()
    import_rfm_records(db_session, rows)
    _make_doc_with_person_fact(db_session, "Иванов Иван Иванович")

    generate_matches(db_session)
    stats2 = generate_matches(db_session)
    assert stats2["already_existed"] >= 1


def test_generate_matches_preserves_confirmed(db_session):
    rows = load_fixture_rows()
    import_rfm_records(db_session, rows)
    _make_doc_with_person_fact(db_session, "Иванов Иван Иванович")

    generate_matches(db_session)

    # Confirm the first candidate
    candidates = repo.list_match_candidates(db_session)
    assert len(candidates) >= 1
    repo.update_match_status(db_session, candidates[0].id, "confirmed", "test")

    # Re-generate — should not overwrite confirmed status
    generate_matches(db_session)
    c = repo.get_match_candidate(db_session, candidates[0].id)
    assert c.status == "confirmed"


def test_oblique_case_candidate_no_match(db_session):
    """Oblique case surnames don't match — known limitation without morphological analysis."""
    rows = load_fixture_rows()
    import_rfm_records(db_session, rows)
    _make_doc_with_person_fact(db_session, "Иванова Ивана Ивановича")

    stats = generate_matches(db_session)
    assert stats["candidates_created"] == 0  # "иванова" != "иванов"


def test_yo_e_candidate(db_session):
    """ё→е normalization allows matching."""
    rec = PersonRecord(
        source="rfm",
        raw_name="Семёнов Пётр Алексеевич",
        search_name="семенов петр алексеевич",
        normalized_name="семенов петр алексеевич",
        normalization_confidence=0.95,
        normalization_method="lowercase",
        birth_date="1985-03-10",
    )
    db_session.add(rec)
    db_session.flush()

    # Use nominative case so surname matches
    _make_doc_with_person_fact(db_session, "Семенов Петр Алексеевич")

    stats = generate_matches(db_session)
    assert stats["candidates_created"] >= 1


def test_reasons_json_matches_score(db_session):
    rows = load_fixture_rows()
    import_rfm_records(db_session, rows)
    _make_doc_with_person_fact(db_session, "Иванов Иван Иванович")

    generate_matches(db_session)
    candidates = repo.list_match_candidates(db_session)
    assert len(candidates) >= 1

    c = candidates[0]
    reasons = json.loads(c.reasons_json)
    total_reason_impact = sum(r.get("impact", 0) for r in reasons)
    # Score should be consistent with reasons
    assert abs(total_reason_impact - c.score) < 0.5  # allow for clamping


def test_list_matches(db_session):
    rows = load_fixture_rows()
    import_rfm_records(db_session, rows)
    _make_doc_with_person_fact(db_session, "Иванов Иван Иванович")
    generate_matches(db_session)

    candidates = repo.list_match_candidates(db_session)
    assert len(candidates) >= 1

    pending = repo.list_match_candidates(db_session, status="pending")
    assert len(pending) >= 1


def test_update_match_status(db_session):
    rows = load_fixture_rows()
    import_rfm_records(db_session, rows)
    _make_doc_with_person_fact(db_session, "Иванов Иван Иванович")
    generate_matches(db_session)

    candidates = repo.list_match_candidates(db_session)
    c = candidates[0]

    updated = repo.update_match_status(db_session, c.id, "confirmed", "looks good")
    assert updated.status == "confirmed"
    assert updated.review_comment == "looks good"
    assert updated.reviewed_at is not None
