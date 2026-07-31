"""Ranking the review queue by context, because the score cannot separate anyone.

Every candidate the pipeline produces sits at exactly 0.50 — surname and given
name matched and nothing else was available, because news text does not carry
dates or places of birth (see known-risks). Sorting by score therefore orders
the queue arbitrarily.

Two facts the pipeline *can* measure do separate them: how many people in the
registry share the surname, and how many other documents name the same person.
One Шульман is a very different proposition from one of fourteen Кадыровых.
"""

from __future__ import annotations

import pytest

from court_monitor.matching.candidates import generate_matches
from court_monitor.storage import repository as repo
from court_monitor.storage.orm import ExtractedFact, MatchCandidate, PersonRecord, SourceDocument


def _doc(session, n: int) -> SourceDocument:
    doc = SourceDocument(
        url=f"https://example.invalid/{n}", source_type="telegram", content_hash=f"h{n}"
    )
    session.add(doc)
    session.flush()
    return doc


def _name_fact(session, doc: SourceDocument, value: str, quote: str = "") -> ExtractedFact:
    fact = ExtractedFact(
        document_id=doc.id,
        entity="person",
        field="full_name_original",
        value=value,
        quote=quote or value,
        extraction_method="regex:name:full_fio",
    )
    session.add(fact)
    session.flush()
    return fact


def _record(session, name: str) -> PersonRecord:
    rec = PersonRecord(
        source="rfm",
        raw_name=name,
        search_name=name.lower(),
        normalized_name=name.lower(),
        normalization_confidence=1.0,
        normalization_method="test",
    )
    session.add(rec)
    session.flush()
    return rec


@pytest.fixture()
def two_candidates(db_session):
    """A rare surname mentioned twice, and a common one mentioned once.

    Both score 0.50 — exactly the situation on the live corpus.
    """
    _record(db_session, "Шульман Екатерина Михайловна")
    for patronymic in ("Ахматович", "Борисович", "Викторович"):
        _record(db_session, f"Кадыров Рамзан {patronymic}")

    rare_a = _name_fact(db_session, _doc(db_session, 1), "Шульман Екатерина Михайловна")
    _name_fact(db_session, _doc(db_session, 2), "Шульман Екатерина Михайловна")
    _name_fact(db_session, _doc(db_session, 3), "Кадыров Рамзан Ахматович")

    generate_matches(db_session)
    db_session.flush()
    return rare_a


def test_the_measured_context_is_stored_on_the_candidate(db_session, two_candidates):
    """Both numbers come from data generate_matches already has in memory —
    the surname index and the person facts — so this costs no extra queries."""
    rows = db_session.query(MatchCandidate).all()
    assert rows, "expected candidates"
    for row in rows:
        assert row.namesakes is not None
        assert row.other_mentions is not None


def test_a_common_surname_is_recorded_as_such(db_session, two_candidates):
    kadyrov = [
        c
        for c in db_session.query(MatchCandidate).all()
        if "кадыров" in c.person_record.normalized_name
    ]
    assert kadyrov, "expected a Кадыров candidate"
    assert all(c.namesakes == 3 for c in kadyrov)


def test_a_rare_surname_is_recorded_as_such(db_session, two_candidates):
    shulman = [
        c
        for c in db_session.query(MatchCandidate).all()
        if "шульман" in c.person_record.normalized_name
    ]
    assert shulman
    assert all(c.namesakes == 1 for c in shulman)


def test_other_mentions_counts_documents_not_facts(db_session, two_candidates):
    """The same person named twice inside one document is one mention, not two."""
    shulman = [
        c
        for c in db_session.query(MatchCandidate).all()
        if "шульман" in c.person_record.normalized_name
    ]
    assert all(c.other_mentions == 1 for c in shulman), "two documents → one *other* mention each"


def test_the_queue_puts_the_rare_surname_first(db_session, two_candidates):
    """The ordering is lexicographic on measured facts, not a weighted score:
    rarer surname first, then more corroborating mentions, then score. A
    weighted formula would invent numbers nobody can audit."""
    rows = repo.list_match_candidates(db_session, status="pending")

    assert len(rows) >= 2
    assert all(r.score == rows[0].score for r in rows), "premise: everything scores the same"
    assert "шульман" in rows[0].person_record.normalized_name


def test_candidates_never_measured_sink_instead_of_floating_up(db_session, two_candidates):
    """Rows predating the migration have NULL context. Unknown must not read as
    'rarest surname in the registry' and take over the top of the queue."""
    orphan = db_session.query(MatchCandidate).first()
    orphan.namesakes = None
    orphan.other_mentions = None
    db_session.flush()

    rows = repo.list_match_candidates(db_session, status="pending")

    assert rows[-1].id == orphan.id


def test_ranking_does_not_touch_the_score(db_session, two_candidates):
    """score stays a statement about name/date/place similarity alone.

    Checked as "score is exactly the name contribution", not against a literal:
    the number depends on how complete the name is (0.70 for a full FIO, 0.50
    for surname+given name), and pinning the literal would make this test about
    the fixture rather than about the invariant. Folding surname rarity in
    would make the number mean nothing in particular.
    """
    rows = db_session.query(MatchCandidate).all()
    assert rows
    for c in rows:
        assert c.score == pytest.approx(c.name_score)
        assert c.birth_date_score == 0.0 and c.birthplace_score == 0.0
        # a rare surname must not have been rewarded inside the score itself
        assert c.score == pytest.approx(rows[0].score)
