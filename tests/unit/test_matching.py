"""Tests: person matching — morphological normalization, scoring, candidates."""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from court_monitor.domain.models import VerificationStatus
from court_monitor.matching.candidates import _extract_year_from_text, generate_matches
from court_monitor.matching.name_normalizer import normalize_name_morph
from court_monitor.matching.score import (
    CANDIDATE_THRESHOLD,
    BirthDateEvidence,
    score_match,
)
from court_monitor.services import import_rfm_records
from court_monitor.sources.fedsfm import parse_file
from court_monitor.storage import repository as repo
from court_monitor.storage.orm import (
    Base,
    ExtractedFact,
    SourceDocument,
)


def _make_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    return factory()


def _add_person_fact(session, name: str, quote: str = None) -> ExtractedFact:
    """Create a document with a person fact."""
    doc = SourceDocument(
        url="https://test/doc",
        source_type="sudrf",
        content_hash=f"hash_{hash(name)}",
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
        quote=quote or f"...{name}...",
        extraction_method="regex:name",
    )
    session.add(fact)
    session.commit()
    return fact


# ======================================================================
# Morphological normalization tests
# ======================================================================


def test_morph_nominative():
    """Full nominative — no morphological change needed."""
    n = normalize_name_morph("Иванов Иван Иванович")
    assert n.surname == "иванов"
    assert n.name == "иван"
    assert n.patronymic == "иванович"
    assert n.method == "ru-name-v2"
    assert n.nominative == "иванов иван иванович"


def test_morph_oblique_masculine():
    """Masculine oblique → nominative."""
    n = normalize_name_morph("Иванова Ивана Ивановича")
    assert n.surname == "иванов"
    assert n.name == "иван"
    assert n.patronymic == "иванович"
    assert n.nominative == "иванов иван иванович"


def test_morph_oblique_feminine():
    """Feminine oblique → nominative."""
    n = normalize_name_morph("Петрову Марию Сергеевну")
    assert n.surname == "петрова"
    assert n.name == "мария"
    assert n.patronymic == "сергеевна"
    assert n.nominative == "петрова мария сергеевна"


def test_morph_oblique_dative():
    """Dative case → nominative."""
    n = normalize_name_morph("Абдуллаеву Рустаму Камиловичу")
    assert n.surname == "абдуллаев"
    assert n.name == "рустам"
    assert n.patronymic == "камилович"


def test_morph_oblique_instrumental():
    """Instrumental case → nominative."""
    n = normalize_name_morph("Ивановым Иваном Ивановичем")
    assert n.surname == "иванов"
    assert n.name == "иван"
    assert n.patronymic == "иванович"


def test_morph_oblique_genitive():
    """Genitive case → nominative."""
    n = normalize_name_morph("Ивановой Марии Сергеевны")
    assert n.surname == "иванова"  # -овой → -ова
    assert n.name == "мария"
    assert n.patronymic == "сергеевна"


def test_morph_natalya():
    """Наталью → Наталья (accusative for -ья names)."""
    n = normalize_name_morph("Наталью Сергеевну Ивановну")
    assert n.surname == "наталья"
    assert n.name == "сергеевна"
    assert n.patronymic == "ивановна"


def test_morph_sofya():
    """Софью → Софья (accusative for -ья names)."""
    n = normalize_name_morph("Софью Александровну Петровну")
    assert n.surname == "софья"
    assert n.name == "александровна"
    assert n.patronymic == "петровна"


def test_morph_initials():
    """Initials — no morphological change, but goes through morph path."""
    n = normalize_name_morph("Иванов И. И.")
    assert n.surname == "иванов"
    assert n.method == "ru-name-v2"
    assert n.nominative == "иванов и. и."


def test_morph_two_tokens():
    """Two tokens — no morphological change."""
    n = normalize_name_morph("Иванов Иван")
    assert n.surname == "иванов"
    assert n.name == "иван"
    assert n.method == "ru-name-v2-simple"


def test_morph_yo_e():
    """ё → е normalization."""
    n = normalize_name_morph("Семёнов Пётр Алексеевич")
    assert n.surname == "семенов"
    assert n.name == "петр"
    assert n.patronymic == "алексеевич"


def test_morph_hyphenated():
    """Hyphenated surname — normalize_fio splits hyphens."""
    n = normalize_name_morph("Иванов-Петров Иван Иванович")
    # normalize_fio splits hyphens into separate tokens
    assert "иванов" in n.surname or "иванов" in n.nominative


def test_morph_confidence():
    """Morphological transformation has slightly lower confidence."""
    n_nom = normalize_name_morph("Иванов Иван Иванович")
    n_obl = normalize_name_morph("Иванова Ивана Ивановича")
    assert n_nom.confidence >= n_obl.confidence


# ======================================================================
# Scoring tests
# ======================================================================


def test_full_match_after_morph():
    """Oblique case matches nominative after morphological normalization."""
    doc = normalize_name_morph("Иванова Ивана Ивановича")
    rec = normalize_name_morph("Иванов Иван Иванович")
    result = score_match(doc, rec, None, None)
    assert result.score >= 0.6  # full name match after morph


def test_year_match():
    """Same birth year → positive score."""
    doc = normalize_name_morph("Иванов Иван Иванович")
    rec = normalize_name_morph("Иванов Иван Иванович")
    result = score_match(doc, rec, BirthDateEvidence.from_year("1983"), "1983-06-15")
    assert result.birth_date_score > 0
    assert any(r["rule"] == "birth_year_match" for r in result.reasons)


def test_year_conflict():
    """Different birth years → hard conflict."""
    doc = normalize_name_morph("Иванов Иван Иванович")
    rec = normalize_name_morph("Иванов Иван Иванович")
    result = score_match(doc, rec, BirthDateEvidence.from_year("1983"), "1980-01-01")
    assert result.birth_date_score < 0, "Year conflict should give negative score"
    assert any(c["rule"] == "birth_year_conflict" for c in result.conflicts)
    # Year conflict should make total score below threshold
    assert result.score < CANDIDATE_THRESHOLD


def test_missing_birth_date():
    """Missing birth date is not a conflict."""
    doc = normalize_name_morph("Иванов Иван Иванович")
    rec = normalize_name_morph("Иванов Иван Иванович")
    result = score_match(doc, rec, None, "1980-01-01")
    assert result.birth_date_score == 0.0
    assert not any(c["rule"] == "birth_year_conflict" for c in result.conflicts)


def test_full_date_match():
    """Full date match → higher score."""
    doc = normalize_name_morph("Иванов Иван Иванович")
    rec = normalize_name_morph("Иванов Иван Иванович")
    result = score_match(doc, rec, BirthDateEvidence.from_full_date("1983-01-01"), "1983-01-01")
    assert result.birth_date_score == 0.20
    assert any(r["rule"] == "birth_date_match" for r in result.reasons)


def test_different_surname():
    """Different surname → zero score."""
    doc = normalize_name_morph("Иванов Иван Иванович")
    rec = normalize_name_morph("Петров Иван Иванович")
    result = score_match(doc, rec, None, None)
    assert result.score == 0.0


# ======================================================================
# Negative integration test: 1983 vs 1980 → no candidate
# ======================================================================


def test_negative_no_candidate_year_conflict():
    """Oblique case + year conflict → no candidate created."""
    session = _make_session()
    try:
        # Import RFM record with birth_date 1980
        result = parse_file(Path("tests/fixtures/rfm/persons.xml"))
        import_rfm_records(session, result.rows, source="rfm")
        session.commit()

        # Add document fact with oblique name and year 1983
        _add_person_fact(
            session,
            "Иванова Ивана Ивановича",
            quote="Иванова Ивана Ивановича, 1983 года рождения",
        )

        stats = generate_matches(session)
        # Year conflict (1983 vs 1980) should prevent candidate creation
        assert stats["candidates_created"] == 0
        assert stats["no_candidates"] >= 1
    finally:
        session.close()


# ======================================================================
# Positive integration test: 1983 vs 1983 → candidate created
# ======================================================================


def test_positive_candidate_year_match():
    """Oblique case + same year → candidate created."""
    session = _make_session()
    try:
        # Import positive fixture (birth year 1983)
        result = parse_file(__import__("pathlib").Path("tests/fixtures/rfm/persons_match.xml"))
        import_rfm_records(session, result.rows, source="rfm")
        session.commit()

        # Add document fact with oblique name
        _add_person_fact(
            session,
            "Иванова Ивана Ивановича",
            quote="Иванова Ивана Ивановича, 1983 года рождения",
        )

        stats = generate_matches(session)
        assert stats["candidates_created"] >= 1

        # Verify candidate details
        candidates = repo.list_match_candidates(session)
        assert len(candidates) >= 1
        c = candidates[0]
        assert c.status == "pending"
        assert c.score >= CANDIDATE_THRESHOLD

        # Check reasons contain morphological match and birth date match
        reasons = json.loads(c.reasons_json)
        assert any(r["rule"] == "full_name_morphological_match" for r in reasons)
        assert any(r["rule"] in ("birth_date_match", "birth_year_match") for r in reasons)

        # Confirm the candidate
        repo.update_match_status(session, c.id, "confirmed", "looks correct")
        session.commit()

        c_refreshed = repo.get_match_candidate(session, c.id)
        assert c_refreshed.status == "confirmed"
        assert c_refreshed.review_comment == "looks correct"
    finally:
        session.close()


def test_idempotent_no_duplicates():
    """Second generate-matches does not create duplicates."""
    session = _make_session()
    try:
        result = parse_file(__import__("pathlib").Path("tests/fixtures/rfm/persons_match.xml"))
        import_rfm_records(session, result.rows, source="rfm")
        session.commit()

        _add_person_fact(
            session,
            "Иванова Ивана Ивановича",
            quote="Иванова Ивана Ивановича, 1983 года рождения",
        )

        stats1 = generate_matches(session)
        session.commit()
        assert stats1["candidates_created"] >= 1

        stats2 = generate_matches(session)
        session.commit()
        assert stats2["already_existed"] >= 1
        assert stats2["candidates_created"] == 0
    finally:
        session.close()


def test_confirmed_not_overwritten():
    """Confirmed status is preserved on re-generation."""
    session = _make_session()
    try:
        result = parse_file(__import__("pathlib").Path("tests/fixtures/rfm/persons_match.xml"))
        import_rfm_records(session, result.rows, source="rfm")
        session.commit()

        _add_person_fact(
            session,
            "Иванова Ивана Ивановича",
            quote="Иванова Ивана Ивановича, 1983 года рождения",
        )

        generate_matches(session)
        session.commit()

        candidates = repo.list_match_candidates(session)
        repo.update_match_status(session, candidates[0].id, "confirmed", "ok")
        session.commit()

        # Re-generate — confirmed should not change
        generate_matches(session)
        session.commit()

        c = repo.get_match_candidate(session, candidates[0].id)
        assert c.status == "confirmed"
    finally:
        session.close()


def test_year_extraction_from_quote():
    """Year extracted from quote '1983 года рождения'."""
    assert _extract_year_from_text("Иванова, 1983 года рождения") == "1983"
    assert _extract_year_from_text("Петров 1985 г.р.") == "1985"
    assert _extract_year_from_text("Сидоров, рожд. 1990") == "1990"
    assert _extract_year_from_text("без даты") is None
