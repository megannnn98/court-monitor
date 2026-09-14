from extraction.models import EventMention, ExtractionDocument, NormalizedMention, RawMention


class ExtractionValidationError(ValueError):
    pass


def validate_raw_mention(document: ExtractionDocument, mention: RawMention) -> None:
    _validate_span(
        text=document.text,
        surface_text=mention.surface_text,
        start_offset=mention.start_offset,
        end_offset=mention.end_offset,
    )


def validate_normalized_mention(
    document: ExtractionDocument,
    mention: NormalizedMention,
) -> None:
    _validate_span(
        text=document.text,
        surface_text=mention.surface_text,
        start_offset=mention.start_offset,
        end_offset=mention.end_offset,
    )


def validate_event(
    document: ExtractionDocument,
    event: EventMention,
    *,
    mention_count: int,
) -> None:
    if event.end_offset > len(document.text):
        raise ExtractionValidationError("event span exceeds document text")
    for link in event.links:
        if link.mention_index >= mention_count:
            raise ExtractionValidationError("event link mention_index is out of range")


def _validate_span(
    *,
    text: str,
    surface_text: str,
    start_offset: int,
    end_offset: int,
) -> None:
    if start_offset < 0:
        raise ExtractionValidationError("start_offset must be non-negative")
    if end_offset > len(text):
        raise ExtractionValidationError("end_offset exceeds document text")
    if start_offset >= end_offset:
        raise ExtractionValidationError("start_offset must be less than end_offset")
    if not surface_text:
        raise ExtractionValidationError("surface_text must not be empty")
    if text[start_offset:end_offset] != surface_text:
        raise ExtractionValidationError("span does not match surface_text")
