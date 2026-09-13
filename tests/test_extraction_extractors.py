from extraction_documents import build_extraction_document_from_parsed_article
from extraction_events import RuleBasedEventExtractor
from extraction_extractors import RuleBasedEntityExtractor
from extraction_models import EntityType, EventType, ExtractionDocument
from extraction_normalizers import RuleBasedMentionNormalizer
from models import ParsedArticle


def make_document(text: str) -> ExtractionDocument:
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


def test_extracts_legal_reference_person_court_and_event() -> None:
    text = (
        "Басманный районный суд Москвы заочно арестовал Александра Иванова по ч. 2 ст. 207.3 УК РФ."
    )
    document = make_document(text)
    extractor = RuleBasedEntityExtractor()

    mentions = extractor.extract(document)

    assert (EntityType.COURT, "Басманный районный суд Москвы") in {
        (mention.entity_type, mention.surface_text) for mention in mentions
    }
    assert (EntityType.PERSON, "Александра Иванова") in {
        (mention.entity_type, mention.surface_text) for mention in mentions
    }
    assert (EntityType.LEGAL_REFERENCE, "ч. 2 ст. 207.3 УК РФ") in {
        (mention.entity_type, mention.surface_text) for mention in mentions
    }
    assert [document.text[m.start_offset : m.end_offset] for m in mentions] == [
        m.surface_text for m in mentions
    ]


def test_extracts_supported_legal_reference_variants() -> None:
    text = (
        "Дело возбудили по ст. 207.3 УК РФ. "
        "Следствие упомянуло п. «а» ч. 2 ст. 282 УК РФ. "
        "В протоколе остался КоАП РФ."
    )

    mentions = RuleBasedEntityExtractor().extract(make_document(text))

    surfaces = [mention.surface_text for mention in mentions]
    assert "ст. 207.3 УК РФ" in surfaces
    assert "п. «а» ч. 2 ст. 282 УК РФ" in surfaces
    assert "КоАП РФ" in surfaces


def test_normalizes_person_and_legal_reference() -> None:
    document = make_document("Александра Иванова обвинили по ч. 2 ст. 207.3 УК РФ.")
    normalizer = RuleBasedMentionNormalizer()
    mentions = RuleBasedEntityExtractor().extract(document)

    normalized = [normalizer.normalize(mention, document) for mention in mentions]

    assert ("person", "Александр Иванов") in {
        (mention.entity_type.value, mention.normalized_text) for mention in normalized
    }
    legal = next(
        mention for mention in normalized if mention.entity_type is EntityType.LEGAL_REFERENCE
    )
    assert legal.normalized_data.model_dump()["article"] == "207.3"
    assert legal.normalized_data.model_dump()["part"] == "2"


def test_extracts_events_with_links() -> None:
    document = make_document(
        "Следственный комитет задержал Марии Петровой в Москве по ст. 282 УК РФ."
    )
    normalizer = RuleBasedMentionNormalizer()
    mentions = [
        normalizer.normalize(mention, document)
        for mention in RuleBasedEntityExtractor().extract(document)
    ]

    events = RuleBasedEventExtractor().extract(document, mentions)

    assert [event.event_type for event in events] == [EventType.DETENTION]
    assert events[0].links


def test_order_is_deterministic() -> None:
    document = make_document("МВД задержало Ивана Петрова. МВД задержало Ивана Петрова.")
    extractor = RuleBasedEntityExtractor()

    first = extractor.extract(document)
    second = extractor.extract(document)

    assert first == second
