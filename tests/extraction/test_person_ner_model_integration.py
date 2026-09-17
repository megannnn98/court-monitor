"""The real person recognizer model (opt-in, downloads and loads the model).

uv sync --group ner
PERSON_NER_MODEL_TESTS=1 uv run pytest -m person_ner_model

These cover the contract the rest of the pipeline relies on — offsets that point at the
text, deterministic ordering — and the hard negatives the rule extractor gets wrong, which
are the reason for the model in the first place.
"""

from __future__ import annotations

import os

import pytest

from extraction.person_ner.config import PersonNerSettings
from extraction.person_ner.gliner_recognizer import GlinerPersonNameRecognizer
from extraction.person_ner.models import PersonNameSpan

pytestmark = [
    pytest.mark.person_ner_model,
    pytest.mark.skipif(
        os.environ.get("PERSON_NER_MODEL_TESTS") != "1", reason="PERSON_NER_MODEL_TESTS=1 not set"
    ),
]


@pytest.fixture(scope="module")
def recognizer() -> GlinerPersonNameRecognizer:
    """Loaded once for the whole module: the model must not be read per call."""
    settings = PersonNerSettings.from_env({})
    return GlinerPersonNameRecognizer(settings.model_id, min_score=settings.min_score)


def _names(recognizer: GlinerPersonNameRecognizer, text: str) -> list[str]:
    return [span.surface_text for span in recognizer.recognize(text)]


def test_one_person_is_found_whole(recognizer: GlinerPersonNameRecognizer) -> None:
    assert _names(recognizer, "Ивана Петрова задержали у здания суда.") == ["Ивана Петрова"]


def test_several_people_in_one_sentence_are_separate_spans(
    recognizer: GlinerPersonNameRecognizer,
) -> None:
    names = _names(recognizer, "Иван Петров и Алексей Сидоров подали жалобу.")

    assert names == ["Иван Петров", "Алексей Сидоров"]


def test_an_inflected_name_keeps_the_form_the_article_used(
    recognizer: GlinerPersonNameRecognizer,
) -> None:
    """The surface form is what the text says; normalization is a later layer."""
    assert _names(recognizer, "Приговор вынесли в отношении Андрея Кузнецова.") == [
        "Андрея Кузнецова"
    ]


def test_initials_are_part_of_the_name(recognizer: GlinerPersonNameRecognizer) -> None:
    names = _names(recognizer, "И. И. Иванов и И. Петров подали иск.")

    assert names == ["И. И. Иванов", "И. Петров"]


def test_a_role_before_the_name_is_not_part_of_it(
    recognizer: GlinerPersonNameRecognizer,
) -> None:
    for text, expected in (
        ("Судья Александр Сенькин вынес приговор.", "Александр Сенькин"),
        ("Адвокат Иван Павлов сообщил о деле.", "Иван Павлов"),
        ("Журналист Сергей Мингазов вышел из СИЗО.", "Сергей Мингазов"),
    ):
        assert _names(recognizer, text) == [expected], text


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("Европейский суд по правам человека принял жалобу.", id="court"),
        pytest.param("Басманный районный суд продлил меру пресечения.", id="district court"),
        pytest.param("Следственный комитет возбудил уголовное дело.", id="organization"),
        pytest.param("Московский городской суд оставил решение в силе.", id="city court"),
    ],
)
def test_an_institution_is_not_a_person(recognizer: GlinerPersonNameRecognizer, text: str) -> None:
    """Exactly what the capitalized-word patterns get wrong."""
    assert _names(recognizer, text) == []


def test_an_organization_next_to_a_name_does_not_swallow_it(
    recognizer: GlinerPersonNameRecognizer,
) -> None:
    """«Минюста Виталия» was one of the rule extractor's false positives."""
    assert _names(recognizer, "Минюст признал Виталия Иванова иностранным агентом.") == [
        "Виталия Иванова"
    ]


def test_a_place_is_not_a_person(recognizer: GlinerPersonNameRecognizer) -> None:
    names = _names(
        recognizer, "Колония в поселке Харп Ямало-Ненецкого автономного округа приняла его."
    )

    assert names == []


def test_every_span_points_at_its_own_text(recognizer: GlinerPersonNameRecognizer) -> None:
    text = (
        "Иван Петров пришел в суд. Судья Александр Сенькин арестовал Петрова. "
        "Позже адвокат Иван Павлов обжаловал решение по ст. 207.3 УК РФ."
    )

    for span in recognizer.recognize(text):
        assert text[span.start_offset : span.end_offset] == span.surface_text


def test_the_result_is_ordered_by_offsets(recognizer: GlinerPersonNameRecognizer) -> None:
    text = "Сначала задержали Ивана Петрова, затем Алексея Сидорова, потом Марию Кузнецову."

    spans = recognizer.recognize(text)

    assert spans == sorted(spans, key=lambda span: (span.start_offset, span.end_offset))


def test_the_same_text_gives_the_same_spans_twice(
    recognizer: GlinerPersonNameRecognizer,
) -> None:
    text = "Ивана Петрова задержали, позже Ивана Петрова отпустили."

    assert recognizer.recognize(text) == recognizer.recognize(text)


def test_a_repeated_name_is_reported_at_each_place_it_appears(
    recognizer: GlinerPersonNameRecognizer,
) -> None:
    """Offsets must come from the model, not from searching the string back in the text."""
    text = "Ивана Петрова задержали, позже Ивана Петрова отпустили."

    spans = recognizer.recognize(text)

    assert [span.start_offset for span in spans] == [0, 31]


def test_a_text_without_people_yields_nothing(
    recognizer: GlinerPersonNameRecognizer,
) -> None:
    assert recognizer.recognize("Суд продлил срок содержания под стражей на два месяца.") == []


@pytest.mark.parametrize("text", ["", "   ", "\n\n"])
def test_empty_text_yields_nothing(recognizer: GlinerPersonNameRecognizer, text: str) -> None:
    assert recognizer.recognize(text) == []


def test_a_long_article_is_covered_to_its_end(
    recognizer: GlinerPersonNameRecognizer,
) -> None:
    """Truncating the input would lose whoever is named last."""
    filler = "Заседание продолжалось несколько часов подряд. " * 200
    text = f"{filler}В конце заседания выступил Алексей Сидоров."

    names = _names(recognizer, text)

    assert "Алексей Сидоров" in names


def test_a_confident_name_scores_above_the_default_threshold(
    recognizer: GlinerPersonNameRecognizer,
) -> None:
    spans = recognizer.recognize("Ивана Петрова задержали.")

    assert spans and all(isinstance(span, PersonNameSpan) for span in spans)
    assert spans[0].confidence >= 0.5
