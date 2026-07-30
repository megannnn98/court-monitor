"""Cross-extractor name deduplication in the service layer."""

from __future__ import annotations

from court_monitor.domain.facts import ExtractedFactDTO
from court_monitor.services import _dedupe_against


def _name(value: str, method: str = "spacy:ner:per_partial") -> ExtractedFactDTO:
    return ExtractedFactDTO(
        entity="person",
        field="full_name_original",
        value=value,
        extraction_method=method,
    )


def test_drops_candidate_already_found_by_the_other_extractor():
    regex = [_name("Екатерина Шульман", "regex:name:two_tokens")]
    ner = [_name("Екатерина Шульман")]
    assert _dedupe_against(ner, regex) == []


def test_yo_and_ye_spellings_count_as_one_name():
    """Regex emitted "Андрей Воробьев" and NER "Андрей Воробьёв" for the same
    person, producing two candidates against one registry record."""
    regex = [_name("Андрей Воробьев", "regex:name:two_tokens")]
    ner = [_name("Андрей Воробьёв")]
    assert _dedupe_against(ner, regex) == []


def test_case_and_spacing_differences_count_as_one_name():
    regex = [_name("Гарри  Каспаров", "regex:name:two_tokens")]
    ner = [_name("гарри каспаров")]
    assert _dedupe_against(ner, regex) == []


def test_keeps_names_the_other_extractor_missed():
    regex = [_name("Екатерина Шульман", "regex:name:two_tokens")]
    ner = [_name("Екатерина Шульман"), _name("Гарри Каспаров")]
    kept = _dedupe_against(ner, regex)
    assert [f.value for f in kept] == ["Гарри Каспаров"]


def test_deduplicates_within_the_candidate_list_too():
    kept = _dedupe_against([_name("Гарри Каспаров"), _name("Гарри Каспаров")], [])
    assert len(kept) == 1


def test_empty_inputs():
    assert _dedupe_against([], []) == []
    assert _dedupe_against([], [_name("Гарри Каспаров")]) == []


def test_different_people_are_both_kept():
    regex = [_name("Иванов Иван Иванович", "regex:name:full_fio")]
    ner = [_name("Петров Пётр Петрович")]
    assert len(_dedupe_against(ner, regex)) == 1
