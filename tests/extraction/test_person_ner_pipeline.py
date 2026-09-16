"""The extraction path with a recognizer in charge of person names — no model loaded."""

from support.person_ner import FakePersonNameRecognizer

from extraction.documents import build_extraction_document_from_parsed_article
from extraction.extractors import RuleBasedEntityExtractor
from extraction.models import EntityType
from extraction.person_ner.config import PersonExtractionStrategy, PersonNerSettings
from extraction.person_ner.factory import build_entity_extractor
from sources.models import ParsedArticle


def _document(text: str):  # type: ignore[no-untyped-def]
    return build_extraction_document_from_parsed_article(
        article_id=1,
        article=ParsedArticle(
            external_id="1",
            url="https://ovd.info/test",
            title="Test",
            published_at=None,
            text=text,
        ),
        source_name="ОВД-Инфо",
        source_url="https://ovd.info/test",
    )


def _people(extractor: RuleBasedEntityExtractor, text: str) -> list[str]:
    return [
        mention.surface_text
        for mention in extractor.extract(_document(text))
        if mention.entity_type is EntityType.PERSON
    ]


def _types(extractor: RuleBasedEntityExtractor, text: str) -> set[EntityType]:
    return {mention.entity_type for mention in extractor.extract(_document(text))}


def test_the_recognizer_is_the_only_source_of_person_mentions() -> None:
    """The patterns must not add their own people beside the recognizer's.

    The text is one the patterns get wrong on their own: «Харп Ямало-Ненецкого» is a
    place they report as a person.
    """
    text = "Колония в поселке Харп Ямало-Ненецкого автономного округа приняла Ивана Петрова."
    extractor = RuleBasedEntityExtractor(
        person_recognizer=FakePersonNameRecognizer(["Ивана Петрова"])
    )

    assert _people(extractor, text) == ["Ивана Петрова"]


def test_the_rule_extractor_still_owns_every_other_entity_type() -> None:
    text = "Басманный районный суд Москвы арестовал Ивана Петрова по ст. 207.3 УК РФ."
    extractor = RuleBasedEntityExtractor(
        person_recognizer=FakePersonNameRecognizer(["Ивана Петрова"])
    )

    types = _types(extractor, text)

    assert EntityType.PERSON in types
    assert EntityType.COURT in types
    assert EntityType.LEGAL_REFERENCE in types


def test_a_name_inside_an_organization_span_is_not_a_separate_person() -> None:
    """The recognizer may mark a name-like token that the rule layer already owns."""
    text = "Следственный комитет возбудил дело."
    extractor = RuleBasedEntityExtractor(
        person_recognizer=FakePersonNameRecognizer(["Следственный комитет"])
    )

    assert _people(extractor, text) == []


def test_mentions_keep_the_confidence_the_recognizer_reported() -> None:
    text = "Ивана Петрова задержали."
    extractor = RuleBasedEntityExtractor(
        person_recognizer=FakePersonNameRecognizer(["Ивана Петрова"], confidence=0.61)
    )

    person = next(
        mention
        for mention in extractor.extract(_document(text))
        if mention.entity_type is EntityType.PERSON
    )

    assert person.confidence == 0.61


def test_the_two_person_sources_report_different_extractor_versions() -> None:
    """Extraction runs of one source must never be reused for the other."""
    rule_based = RuleBasedEntityExtractor()
    with_model = RuleBasedEntityExtractor(person_recognizer=FakePersonNameRecognizer([]))

    assert rule_based.extractor_version != with_model.extractor_version


def test_offsets_of_every_person_mention_point_at_its_own_text() -> None:
    text = "Ивана Петрова задержали, позже Ивана Петрова отпустили."
    extractor = RuleBasedEntityExtractor(
        person_recognizer=FakePersonNameRecognizer(["Ивана Петрова"])
    )

    for mention in extractor.extract(_document(text)):
        assert text[mention.start_offset : mention.end_offset] == mention.surface_text


def test_the_factory_keeps_the_rule_extractor_by_default() -> None:
    extractor = build_entity_extractor(PersonNerSettings.from_env({}))

    assert _people(extractor, "Минюст признал Виталия Иванова иностранным агентом.") != []
    assert extractor.extractor_version == RuleBasedEntityExtractor.extractor_version


def test_the_factory_uses_the_recognizer_when_the_strategy_asks_for_it() -> None:
    settings = PersonNerSettings.from_env({"PERSON_EXTRACTION_STRATEGY": "ner"})
    extractor = build_entity_extractor(
        settings, recognizer=FakePersonNameRecognizer(["Виталия Иванова"])
    )

    assert settings.strategy is PersonExtractionStrategy.NER
    assert _people(extractor, "Минюст признал Виталия Иванова агентом.") == ["Виталия Иванова"]


def test_the_hybrid_adds_a_single_word_name_the_model_missed() -> None:
    """The model loses inflected bare surnames; the patterns still find them."""
    text = "Алексея Навального задержали. Позже Навального отпустили."
    extractor = RuleBasedEntityExtractor(
        person_recognizer=FakePersonNameRecognizer(["Алексея Навального"]),
        blend_single_word_names=True,
    )

    assert _people(extractor, text) == ["Алексея Навального", "Навального"]


def test_the_hybrid_does_not_take_a_multi_word_pattern_match() -> None:
    """«Харп Ямало-Ненецкого» and «Команда Навального» are the patterns' own mistakes."""
    text = "Колония в поселке Харп Ямало-Ненецкого автономного округа приняла Ивана Петрова."
    extractor = RuleBasedEntityExtractor(
        person_recognizer=FakePersonNameRecognizer(["Ивана Петрова"]),
        blend_single_word_names=True,
    )

    assert _people(extractor, text) == ["Ивана Петрова"]


def test_the_hybrid_keeps_the_model_alone_when_it_is_not_asked_to_blend() -> None:
    text = "Алексея Навального задержали. Позже Навального отпустили."
    extractor = RuleBasedEntityExtractor(
        person_recognizer=FakePersonNameRecognizer(["Алексея Навального"])
    )

    assert _people(extractor, text) == ["Алексея Навального"]


def test_the_hybrid_does_not_report_a_name_already_inside_a_recognized_one() -> None:
    """«Навального» sits inside «Алексея Навального»: one mention, not two."""
    text = "Алексея Навального задержали."
    extractor = RuleBasedEntityExtractor(
        person_recognizer=FakePersonNameRecognizer(["Алексея Навального"]),
        blend_single_word_names=True,
    )

    assert _people(extractor, text) == ["Алексея Навального"]


def test_the_hybrid_skips_a_single_word_that_sits_inside_a_recognized_name() -> None:
    """The patterns find «Петрова» on its own; the model already covered it in a longer
    span, so it must not be reported a second time."""
    text = "Иван Петров пришел. Позже Петрова задержали."
    extractor = RuleBasedEntityExtractor(
        person_recognizer=FakePersonNameRecognizer(["Иван Петров", "Позже Петрова"]),
        blend_single_word_names=True,
    )

    assert _people(extractor, text) == ["Иван Петров", "Позже Петрова"]


def test_the_factory_blends_only_for_the_hybrid_strategy() -> None:
    blended = build_entity_extractor(
        PersonNerSettings.from_env({"PERSON_EXTRACTION_STRATEGY": "hybrid"}),
        recognizer=FakePersonNameRecognizer(["Алексея Навального"]),
    )
    model_only = build_entity_extractor(
        PersonNerSettings.from_env({"PERSON_EXTRACTION_STRATEGY": "ner"}),
        recognizer=FakePersonNameRecognizer(["Алексея Навального"]),
    )
    text = "Алексея Навального задержали. Позже Навального отпустили."

    assert _people(blended, text) == ["Алексея Навального", "Навального"]
    assert _people(model_only, text) == ["Алексея Навального"]
