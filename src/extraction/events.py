from __future__ import annotations

import re

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

# A person introduced by a procedural or professional role takes part in the event
# without being its subject («судья Нефедов арестовал…», «его адвокат рассказал…»).
_NON_SUBJECT_ROLES = (
    "судья",
    "судьи",
    "следователь",
    "следовательница",
    "прокурор",
    "прокурора",
    "дознаватель",
    "дознавательница",
    "адвокат",
    "адвоката",
    "адвокатка",
    "защитник",
    "защитника",
    "защитница",
    "защитницы",
    "врач",
    "начальник",
    "начальница",
    "представитель",
    "представительница",
    "председатель",
    "эксперт",
    "экспертка",
    "юрист",
    "юристка",
)
_ROLE_BEFORE_NAME = re.compile(
    r"(?:^|[\s«(])(?:" + "|".join(_NON_SUBJECT_ROLES) + r")\s+(?:[а-яё-]+\s+){0,2}$",
    re.IGNORECASE,
)
_ROLE_AS_FIRST_TOKEN = re.compile(r"^(?:" + "|".join(_NON_SUBJECT_ROLES) + r")\s", re.IGNORECASE)
# The person reports the event rather than undergoes it.
_SOURCE_BEFORE_NAME = re.compile(
    r"(?:по данным|по словам|со слов|как сообщил[аи]?)\s+(?:[а-яё-]+\s+){0,3}$", re.IGNORECASE
)
_SPEECH_AFTER_NAME = re.compile(
    r"^[\s,»\")]*(?:[а-яё-]+\s+){0,1}(?:сообщил[аи]?|рассказал[аи]?|заявил[аи]?|"
    r"отметил[аи]?|добавил[аи]?|пишет|говорит|считает|уточнил[аи]?)\b",
    re.IGNORECASE,
)
_CONTEXT_CHARS = 60

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
    extractor_version = "1.1.0"

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
            links = self._links_for_sentence(mentions, sentence_start, sentence_end, document.text)
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
        text: str = "",
    ) -> list[EventEntityLink]:
        links: list[EventEntityLink] = []
        for index, mention in enumerate(mentions):
            if mention.start_offset < start or mention.end_offset > end:
                continue
            if mention.entity_type is EntityType.PERSON and _is_non_subject(
                text, mention, start, end
            ):
                continue
            role = _role_for_entity_type(mention.entity_type)
            links.append(EventEntityLink(role=role, mention_index=index))
        return links


def _is_non_subject(text: str, mention: NormalizedMention, start: int, end: int) -> bool:
    """A reporting source or a procedural actor in the event sentence: not its subject.

    Ambiguous cases stay unlinked: a wrongly attributed event is worse than a missed one.
    """
    if not text:
        return False
    before = text[max(start, mention.start_offset - _CONTEXT_CHARS) : mention.start_offset]
    after = text[mention.end_offset : min(end, mention.end_offset + _CONTEXT_CHARS)]
    return bool(
        _ROLE_AS_FIRST_TOKEN.match(mention.surface_text)
        or _ROLE_BEFORE_NAME.search(before)
        or _SOURCE_BEFORE_NAME.search(before)
        or _SPEECH_AFTER_NAME.match(after)
    )


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
