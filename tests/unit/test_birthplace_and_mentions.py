"""Birthplace scoring end-to-end, and the indexed 'other mentions' lookup.

Both cover things that used to be plumbed but not reachable: the birthplace
dimension could never fire because nothing extracted a place from a document,
and other-mentions matched in Python over a capped 2000-row read.
"""

from __future__ import annotations

import pytest

from court_monitor.matching.candidates import _extract_place_from_fact
from court_monitor.matching.name_normalizer import normalize_name_morph
from court_monitor.matching.score import W_BIRTHPLACE_MATCH, _place_key, score_match
from court_monitor.storage import repository as repo
from court_monitor.storage.orm import ExtractedFact, SourceDocument


class _Quoted:
    """Minimal stand-in for an ExtractedFact — the extractor only reads .quote."""

    def __init__(self, quote: str | None) -> None:
        self.quote = quote


# ---------------------------------------------------------------------------
# Extracting a birth place from the quote around a name
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("quote", "expected"),
    [
        ("Иванов Иван Иванович, 1983 года рождения, уроженец г. Москвы, осужден", "г. Москвы"),
        ("уроженка Республики Дагестан, признана виновной", "Республики Дагестан"),
        ("Петров родился в г. Грозном и позднее переехал", "г. Грозном"),
        ("уроженцем с. Ново-Ивановское;", "с. Ново-Ивановское"),
    ],
)
def test_birth_place_is_read_from_the_quote(quote, expected):
    assert _extract_place_from_fact(_Quoted(quote)) == expected


@pytest.mark.parametrize(
    "quote",
    [
        None,
        "",
        "просто текст без места рождения",
        "Сидоров, уроженец, признан виновным",  # marker with nothing after it
    ],
)
def test_no_place_is_invented_when_the_quote_has_none(quote):
    assert _extract_place_from_fact(_Quoted(quote)) is None


def test_a_runaway_match_is_refused_rather_than_stored():
    """The regex must not swallow a following clause and call it a place."""
    quote = "уроженец очень длинной последовательности слов подряд без всякой пунктуации"
    assert _extract_place_from_fact(_Quoted(quote)) is None


# ---------------------------------------------------------------------------
# The document and the registry write places differently
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("document_form", "registry_form"),
    [
        ("г. Москвы", "Г. МОСКВА"),  # genitive vs nominative
        ("Республики Дагестан", "РЕСПУБЛИКА ДАГЕСТАН"),
        ("Грозном", "Г. ГРОЗНЫЙ"),  # prepositional vs nominative adjective
        ("Махачкале", "Г. МАХАЧКАЛА"),
        ("Московской области", "МОСКОВСКАЯ ОБЛАСТЬ"),
        ("Уфа", "Г. УФА"),  # short name must survive stemming intact
    ],
)
def test_the_two_spellings_of_one_place_converge(document_form, registry_form):
    assert _place_key(document_form) == _place_key(registry_form)


@pytest.mark.parametrize(
    ("a", "b"),
    [("Москвы", "П. МАМЕДКАЛА"), ("Грозного", "Г. МОСКВА"), ("Уфы", "УХТА")],
)
def test_different_places_stay_different(a, b):
    assert _place_key(a) != _place_key(b)


def test_a_bare_settlement_prefix_counts_as_no_place():
    """ "г." on its own names nothing; scoring it as a mismatch would be wrong."""
    assert _place_key("г.") == ""


# ---------------------------------------------------------------------------
# …and the score reflects it
# ---------------------------------------------------------------------------


def test_matching_birthplace_adds_its_weight_across_the_two_spellings():
    doc = normalize_name_morph("Иванов Иван Иванович")
    rec = normalize_name_morph("Иванов Иван Иванович")

    result = score_match(
        doc, rec, None, None, doc_birth_place="г. Москвы", record_birth_place="Г. МОСКВА"
    )

    assert result.birthplace_score == W_BIRTHPLACE_MATCH
    assert any(r["rule"] == "birthplace_match" for r in result.reasons)


def test_a_place_only_the_registry_knows_is_reported_as_missing_not_wrong():
    doc = normalize_name_morph("Иванов Иван Иванович")
    rec = normalize_name_morph("Иванов Иван Иванович")

    result = score_match(doc, rec, None, None, record_birth_place="Г. МОСКВА")

    assert result.birthplace_score == 0.0
    assert any(r["rule"] == "birthplace_missing_in_document" for r in result.reasons)


# ---------------------------------------------------------------------------
# Other mentions of the same person
# ---------------------------------------------------------------------------


def _document(session, doc_id: int) -> SourceDocument:
    doc = SourceDocument(
        url=f"https://example.invalid/{doc_id}",
        source_type="telegram",
        content_hash=f"hash-{doc_id}",
    )
    session.add(doc)
    session.flush()
    return doc


def _name_fact(session, doc: SourceDocument, value: str) -> ExtractedFact:
    fact = ExtractedFact(
        document_id=doc.id,
        entity="person",
        field="full_name_original",
        value=value,
        extraction_method="regex:name:full_fio",
    )
    session.add(fact)
    session.flush()
    return fact


def test_a_fact_built_directly_still_gets_its_normalized_form(db_session):
    """The invariant belongs to the model, not to whoever constructs the row.

    Deriving it in the repository left every other construction path writing a
    row that find_other_mentions could never see.
    """
    doc = _document(db_session, 1)
    fact = _name_fact(db_session, doc, "Воробьёв Андрей")

    assert fact.normalized_value == "воробьев андрей"


def test_a_non_person_fact_carries_no_normalized_value(db_session):
    doc = _document(db_session, 1)
    fact = ExtractedFact(
        document_id=doc.id,
        entity="case",
        field="criminal_article",
        value={"article": "205.1"},
        extraction_method="regex:article:explicit",
    )
    db_session.add(fact)
    db_session.flush()

    assert fact.normalized_value is None


def test_the_same_person_is_found_across_documents_despite_spelling(db_session):
    """ "Воробьёв" and "Воробьев" are one person; the lookup goes through the
    normalized form, so the ё does not hide the second mention."""
    first = _name_fact(db_session, _document(db_session, 1), "Воробьёв Андрей")
    _name_fact(db_session, _document(db_session, 2), "Воробьев Андрей")

    found = repo.find_other_mentions(db_session, "Воробьев Андрей")

    assert len(found) == 2
    assert repo.find_other_mentions(
        db_session, "Воробьев Андрей", exclude_document_id=first.document_id
    ) == [f for f in found if f.document_id != first.document_id]


def test_other_mentions_survive_more_rows_than_the_old_read_cap(db_session):
    """The previous implementation read 2000 rows and filtered them in Python,
    so a corpus past that size silently returned an arbitrary slice."""
    for i in range(2100):
        _name_fact(db_session, _document(db_session, i), f"Однофамилец Номер{i}")
    needle_doc = _document(db_session, 9999)
    _name_fact(db_session, needle_doc, "Шамина Наталья")

    found = repo.find_other_mentions(db_session, "Шамина Наталья")

    assert [f.document_id for f in found] == [needle_doc.id]


def test_an_empty_needle_finds_nothing(db_session):
    _name_fact(db_session, _document(db_session, 1), "Иванов Иван")

    assert repo.find_other_mentions(db_session, "   ") == []
