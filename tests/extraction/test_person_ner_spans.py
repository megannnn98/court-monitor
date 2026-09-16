"""Window, merge and dedup rules of the person recognizer — no model is loaded here."""

from itertools import pairwise

import pytest

from extraction.person_ner.models import PersonNameSpan
from extraction.person_ner.spans import (
    TokenPrediction,
    begins_entity,
    deduplicate,
    is_person_label,
    merge_person_tokens,
    window_bounds,
)


def _tokens(text: str, *pieces: tuple[str, str, float]) -> list[TokenPrediction]:
    """Token predictions for substrings of `text`, found in order."""
    predictions: list[TokenPrediction] = []
    cursor = 0
    for piece, label, score in pieces:
        start = text.index(piece, cursor)
        predictions.append(
            TokenPrediction(label=label, score=score, start=start, end=start + len(piece))
        )
        cursor = start + len(piece)
    return predictions


@pytest.mark.parametrize("label", ["B-PER", "I-PER", "PER", "B-PERSON", "i-per"])
def test_person_labels_are_recognized_whatever_the_prefix_scheme(label: str) -> None:
    assert is_person_label(label)


@pytest.mark.parametrize("label", ["O", "B-ORG", "I-LOC", "MISC", "B-PERIOD"])
def test_other_labels_are_not_persons(label: str) -> None:
    assert not is_person_label(label)


def test_only_a_b_prefix_begins_a_new_entity() -> None:
    assert begins_entity("B-PER")
    assert not begins_entity("I-PER")
    assert not begins_entity("PER")


def test_one_person_is_one_span() -> None:
    text = "Ивана Кузнецова задержали."
    spans = merge_person_tokens(
        text,
        _tokens(text, ("Ивана", "B-PER", 0.99), ("Кузнецова", "I-PER", 0.97)),
        min_score=0.5,
    )

    assert spans == [
        PersonNameSpan(
            start_offset=0, end_offset=15, surface_text="Ивана Кузнецова", confidence=0.97
        )
    ]


def test_the_span_text_is_read_from_the_text_not_rebuilt_from_subwords() -> None:
    """WordPiece pieces glued back together would give «ИванаКузне##цова»."""
    text = "Ивана Кузнецова задержали."
    spans = merge_person_tokens(
        text,
        _tokens(
            text,
            ("Ивана", "B-PER", 0.99),
            ("Кузне", "I-PER", 0.98),
            ("цова", "I-PER", 0.96),
        ),
        min_score=0.5,
    )

    assert [span.surface_text for span in spans] == ["Ивана Кузнецова"]
    assert text[spans[0].start_offset : spans[0].end_offset] == spans[0].surface_text


def test_two_people_side_by_side_stay_two_spans() -> None:
    text = "Иванов, Петров и Сидоров."
    spans = merge_person_tokens(
        text,
        _tokens(
            text, ("Иванов", "B-PER", 0.9), ("Петров", "B-PER", 0.9), ("Сидоров", "B-PER", 0.9)
        ),
        min_score=0.5,
    )

    assert [span.surface_text for span in spans] == ["Иванов", "Петров", "Сидоров"]


def test_a_non_person_token_ends_the_run() -> None:
    text = "Иван Иванов и Следственный комитет."
    spans = merge_person_tokens(
        text,
        _tokens(
            text,
            ("Иван", "B-PER", 0.95),
            ("Иванов", "I-PER", 0.95),
            ("Следственный", "B-ORG", 0.99),
            ("комитет", "I-ORG", 0.99),
        ),
        min_score=0.5,
    )

    assert [span.surface_text for span in spans] == ["Иван Иванов"]


def test_punctuation_between_person_tokens_separates_them() -> None:
    """Without this «Иванов, Петров» becomes one nine-word name."""
    text = "Иванов, Петров"
    spans = merge_person_tokens(
        text,
        _tokens(text, ("Иванов", "I-PER", 0.9), ("Петров", "I-PER", 0.9)),
        min_score=0.5,
    )

    assert [span.surface_text for span in spans] == ["Иванов", "Петров"]


def test_a_span_is_scored_by_its_weakest_token() -> None:
    text = "Иван Иванов"
    spans = merge_person_tokens(
        text,
        _tokens(text, ("Иван", "B-PER", 0.99), ("Иванов", "I-PER", 0.61)),
        min_score=0.5,
    )

    assert spans[0].confidence == pytest.approx(0.61)


def test_a_span_below_the_threshold_is_dropped() -> None:
    text = "Иван Иванов"
    predictions = _tokens(text, ("Иван", "B-PER", 0.55), ("Иванов", "I-PER", 0.55))

    assert merge_person_tokens(text, predictions, min_score=0.5) != []
    assert merge_person_tokens(text, predictions, min_score=0.8) == []


def test_offsets_of_a_window_are_shifted_back_onto_the_article() -> None:
    window = "Ивана Кузнецова задержали."
    spans = merge_person_tokens(
        window,
        _tokens(window, ("Ивана", "B-PER", 0.9), ("Кузнецова", "I-PER", 0.9)),
        min_score=0.5,
        offset=1000,
    )

    assert (spans[0].start_offset, spans[0].end_offset) == (1000, 1015)


def test_text_without_any_person_yields_nothing() -> None:
    text = "Следственный комитет возбудил дело."
    spans = merge_person_tokens(text, _tokens(text, ("Следственный", "B-ORG", 0.99)), min_score=0.5)

    assert spans == []


def test_empty_text_yields_nothing() -> None:
    assert merge_person_tokens("", [], min_score=0.5) == []


def test_a_short_text_is_a_single_window() -> None:
    assert window_bounds(100, window=512, stride=64) == [(0, 100)]


def test_windows_overlap_and_cover_the_whole_text() -> None:
    bounds = window_bounds(1000, window=400, stride=100)

    assert bounds[0][0] == 0
    assert bounds[-1][1] == 1000
    for (_, previous_end), (next_start, _) in pairwise(bounds):
        assert next_start < previous_end, "windows must overlap"


def test_every_character_of_a_long_text_falls_in_some_window() -> None:
    length = 5000
    covered: set[int] = set()
    for start, end in window_bounds(length, window=512, stride=128):
        covered.update(range(start, end))

    assert covered == set(range(length))


@pytest.mark.parametrize(("window", "stride"), [(0, 0), (-1, 0), (100, 100), (100, 200), (100, -1)])
def test_invalid_window_settings_are_rejected(window: int, stride: int) -> None:
    with pytest.raises(ValueError):
        window_bounds(1000, window=window, stride=stride)


def test_the_same_name_seen_by_two_windows_is_kept_once() -> None:
    duplicate = PersonNameSpan(
        start_offset=10, end_offset=21, surface_text="Иван Иванов", confidence=0.9
    )

    assert deduplicate([duplicate, duplicate]) == [duplicate]


def test_a_name_cut_by_a_window_loses_to_the_whole_one() -> None:
    cut = PersonNameSpan(start_offset=10, end_offset=14, surface_text="Иван", confidence=0.99)
    whole = PersonNameSpan(
        start_offset=10, end_offset=21, surface_text="Иван Иванов", confidence=0.88
    )

    assert deduplicate([cut, whole]) == [whole]


def test_distinct_names_are_all_kept_and_ordered() -> None:
    second = PersonNameSpan(start_offset=30, end_offset=36, surface_text="Петров", confidence=0.9)
    first = PersonNameSpan(start_offset=10, end_offset=16, surface_text="Иванов", confidence=0.9)

    assert deduplicate([second, first]) == [first, second]


def test_the_result_is_sorted_by_offsets() -> None:
    spans = [
        PersonNameSpan(start_offset=30, end_offset=36, surface_text="Петров", confidence=0.9),
        PersonNameSpan(start_offset=10, end_offset=16, surface_text="Иванов", confidence=0.9),
        PersonNameSpan(start_offset=20, end_offset=27, surface_text="Сидоров", confidence=0.9),
    ]

    result = deduplicate(spans)

    assert result == sorted(result, key=lambda span: (span.start_offset, span.end_offset))


def test_a_unicode_name_keeps_its_exact_span() -> None:
    text = "Шарль-Андре Жозеф Мари де Голль приехал."
    spans = merge_person_tokens(
        text,
        _tokens(text, ("Шарль-Андре", "B-PER", 0.95), ("Жозеф", "I-PER", 0.95)),
        min_score=0.5,
    )

    assert text[spans[0].start_offset : spans[0].end_offset] == spans[0].surface_text
    assert spans[0].surface_text == "Шарль-Андре Жозеф"


def test_a_span_must_not_be_empty_or_backwards() -> None:
    with pytest.raises(ValueError):
        PersonNameSpan(start_offset=5, end_offset=5, surface_text="", confidence=0.9)
    with pytest.raises(ValueError):
        PersonNameSpan(start_offset=9, end_offset=5, surface_text="x", confidence=0.9)


def test_confidence_must_be_a_probability() -> None:
    with pytest.raises(ValueError):
        PersonNameSpan(start_offset=0, end_offset=1, surface_text="x", confidence=1.5)
