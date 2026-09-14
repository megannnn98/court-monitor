from __future__ import annotations

from extraction.models import (
    EntityType,
    EventEntityLink,
    EventEntityRole,
    EventMention,
    EventType,
    ExtractionDocument,
    NormalizedMention,
)

_ABBREVIATIONS = ("ст.", "ч.", "п.")
_EVENT_KEYWORDS: tuple[tuple[EventType, tuple[str, ...]], ...] = (
    (EventType.CASE_OPENED, ("возбудил", "возбудили", "дело", "деле", "уголовное дело")),
    (EventType.SEARCH, ("обыск", "обыски", "пришли с обыском")),
    (EventType.DETENTION, ("задержал", "задержали", "задержание")),
    (EventType.ARREST, ("арестовал", "арестовали", "арест", "заключил под стражу")),
    (EventType.CHARGE, ("обвинил", "обвинили", "предъявил", "предъявили обвинение")),
    (EventType.SENTENCE, ("приговорил", "приговорили", "приговор")),
    (EventType.FINE, ("оштрафовал", "оштрафовали", "штраф")),
    (EventType.RELEASE, ("освободил", "освободили", "отпустили")),
)


class RuleBasedEventExtractor:
    extractor_name = "rule-based-event-extractor"
    extractor_version = "1.0.0"

    def extract(
        self,
        document: ExtractionDocument,
        mentions: list[NormalizedMention],
    ) -> list[EventMention]:
        events: list[EventMention] = []
        for start, end in _sentence_spans(document.text):
            sentence = document.text[start:end].strip()
            if not sentence:
                continue
            lowered = sentence.lower()
            event_type = self._event_type(lowered)
            if event_type is None:
                continue
            sentence_start = start + document.text[start:end].find(sentence)
            sentence_end = sentence_start + len(sentence)
            links = self._links_for_sentence(mentions, sentence_start, sentence_end)
            events.append(
                EventMention(
                    event_type=event_type,
                    start_offset=sentence_start,
                    end_offset=sentence_end,
                    event_date=document.published_at,
                    confidence=0.72,
                    attributes={"trigger_text": self._trigger_text(lowered)},
                    links=links,
                    extractor_name=self.extractor_name,
                    extractor_version=self.extractor_version,
                )
            )
        return sorted(events, key=lambda event: (event.start_offset, event.event_type.value))

    @staticmethod
    def _event_type(lowered_sentence: str) -> EventType | None:
        for event_type, keywords in _EVENT_KEYWORDS:
            if any(keyword in lowered_sentence for keyword in keywords):
                return event_type
        return None

    @staticmethod
    def _trigger_text(lowered_sentence: str) -> str:
        for _, keywords in _EVENT_KEYWORDS:
            for keyword in keywords:
                if keyword in lowered_sentence:
                    return keyword
        return ""

    @staticmethod
    def _links_for_sentence(
        mentions: list[NormalizedMention],
        start: int,
        end: int,
    ) -> list[EventEntityLink]:
        links: list[EventEntityLink] = []
        for index, mention in enumerate(mentions):
            if mention.start_offset < start or mention.end_offset > end:
                continue
            role = _role_for_entity_type(mention.entity_type)
            links.append(EventEntityLink(role=role, mention_index=index))
        return links


def _role_for_entity_type(entity_type: EntityType) -> EventEntityRole:
    if entity_type is EntityType.PERSON:
        return EventEntityRole.TARGET
    if entity_type is EntityType.COURT:
        return EventEntityRole.COURT
    if entity_type is EntityType.ORGANIZATION:
        return EventEntityRole.AUTHORITY
    if entity_type is EntityType.LEGAL_REFERENCE:
        return EventEntityRole.LEGAL_BASIS
    return EventEntityRole.LOCATION


def _sentence_spans(text: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    start = 0
    for index, char in enumerate(text):
        if char == "\n":
            if start < index:
                spans.append((start, index))
            start = index + 1
            continue
        if char not in ".!?":
            continue
        if (
            char == "."
            and index > 0
            and text[index - 1].isupper()
            and (index == 1 or text[index - 2].isspace())
        ):
            continue
        prefix = text[max(start, index - 4) : index + 1].lower()
        if any(prefix.endswith(abbreviation) for abbreviation in _ABBREVIATIONS):
            continue
        if index + 1 < len(text) and not text[index + 1].isspace():
            continue
        spans.append((start, index + 1))
        start = index + 1
    if start < len(text):
        spans.append((start, len(text)))
    return spans
