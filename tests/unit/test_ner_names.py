"""Unit tests: spaCy NER-based name extraction.

Skipped entirely if spacy or the ru_core_news_lg model isn't installed —
both are an opt-in `nlp` extra (see pyproject.toml), not a hard dependency.
"""

from __future__ import annotations

import pytest

spacy = pytest.importorskip("spacy")

try:
    spacy.load("ru_core_news_lg")
except OSError:
    pytest.skip(
        "ru_core_news_lg not installed — `python -m spacy download ru_core_news_lg`",
        allow_module_level=True,
    )

from court_monitor.extraction.ner_names import extract_name_candidates_ner  # noqa: E402


def test_full_fio_extraction():
    dtos = extract_name_candidates_ner("Иванов Петр Сергеевич совершил преступление.")
    assert any(d.value == "Иванов Петр Сергеевич" for d in dtos)
    full = next(d for d in dtos if d.value == "Иванов Петр Сергеевич")
    assert full.confidence >= 0.85
    assert full.extraction_method == "spacy:ner:per_full"


def test_initials_with_surname():
    dtos = extract_name_candidates_ner("По делу проходит также А. Б. Сидоров.")
    assert any("Сидоров" in d.value for d in dtos)


def test_court_name_not_extracted_as_person():
    dtos = extract_name_candidates_ner("Пресненский районный суд Москвы установил вину.")
    values = [d.value for d in dtos]
    assert not any("суд" in v.lower() for v in values)


def test_empty_text():
    assert extract_name_candidates_ner("") == []


def test_no_duplicates():
    text = "Иванов Петр Сергеевич. Иванов Петр Сергеевич повторился."
    dtos = extract_name_candidates_ner(text)
    values = [d.value for d in dtos]
    assert values.count("Иванов Петр Сергеевич") == 1


def test_quote_present():
    dtos = extract_name_candidates_ner("Иванов Петр Сергеевич задержан сотрудниками полиции.")
    full = next((d for d in dtos if "Иванов" in d.value), None)
    assert full is not None
    assert full.quote and "Иванов" in full.quote


def test_genitive_fio_merged_across_split_entities():
    """The model sometimes splits a genitive-case FIO into two adjacent PER
    spans ("Иванова" / "Петра Сергеевича") — a construction common in court
    reports ("в отношении X возбуждено..."). These must be merged back into
    one candidate, not lost or double-counted."""
    dtos = extract_name_candidates_ner(
        "В отношении Иванова Петра Сергеевича возбуждено уголовное дело."
    )
    assert any(d.value == "Иванова Петра Сергеевича" for d in dtos)
    assert not any(d.value == "Иванова" for d in dtos)


def test_single_token_person_skipped():
    """A bare given name/surname alone is too ambiguous to surface."""
    dtos = extract_name_candidates_ner("Мария рассказала журналистам о случившемся.")
    assert dtos == []
