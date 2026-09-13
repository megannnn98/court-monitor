import pytest
from pydantic import ValidationError

from extraction_models import (
    EntityType,
    EventEntityLink,
    EventEntityRole,
    EventMention,
    EventType,
    ExtractionDocument,
    RawMention,
)
from extraction_validation import ExtractionValidationError, validate_event, validate_raw_mention


def make_document() -> ExtractionDocument:
    return ExtractionDocument(
        article_id=1,
        title="Test",
        text="Басманный суд арестовал Александра Иванова.",
        published_at=None,
        source_name="ОВД-Инфо",
        source_url="https://ovd.info/test",
        content_hash="abc",
    )


def test_validates_matching_span() -> None:
    document = make_document()
    mention = RawMention(
        entity_type=EntityType.PERSON,
        surface_text="Александра Иванова",
        start_offset=document.text.index("Александра"),
        end_offset=len(document.text) - 1,
        confidence=0.9,
    )

    validate_raw_mention(document, mention)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"surface_text": ""},
        {"start_offset": -1},
        {"start_offset": 10, "end_offset": 10},
        {"confidence": 1.5},
    ],
)
def test_raw_mention_model_rejects_invalid_values(kwargs: dict[str, object]) -> None:
    data = {
        "entity_type": EntityType.PERSON,
        "surface_text": "Александр",
        "start_offset": 0,
        "end_offset": 9,
        "confidence": 0.9,
    }
    data.update(kwargs)

    with pytest.raises(ValidationError):
        RawMention.model_validate(data)


def test_validation_rejects_offsets_beyond_text() -> None:
    document = make_document()
    mention = RawMention(
        entity_type=EntityType.PERSON,
        surface_text="Александр",
        start_offset=0,
        end_offset=9,
        confidence=0.9,
    )

    with pytest.raises(ExtractionValidationError, match="span does not match"):
        validate_raw_mention(document, mention)


def test_validation_rejects_event_link_out_of_range() -> None:
    document = make_document()
    event = EventMention(
        event_type=EventType.ARREST,
        start_offset=0,
        end_offset=10,
        confidence=0.9,
        links=[EventEntityLink(role=EventEntityRole.TARGET, mention_index=1)],
    )

    with pytest.raises(ExtractionValidationError, match="out of range"):
        validate_event(document, event, mention_count=1)
