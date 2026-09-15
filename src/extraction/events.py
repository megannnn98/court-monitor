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
    r"отметил[аи]?|добавил[аи]?|писал[аи]?|пишет|пишут|говорит|считает|уточнил[аи]?)\b",
    re.IGNORECASE,
)
_CONTEXT_CHARS = 60

# Triggers match at a word start. Verb (or verb-like) triggers state that the event
# happened in this sentence; noun triggers («приговора», «ареста») often only refer to
# another event, so they count only when no verb trigger is present.
_W = r"(?<![а-яё])"
_VERB_TRIGGERS: tuple[tuple[EventType, str], ...] = (
    (EventType.CASE_OPENED, _W + r"(?:возбу[дж]\w*|завел[аио]?\s+(?:\w+\s+){0,2}дел\w*)"),
    (EventType.SEARCH, _W + r"(?:обыск\w*)"),
    # Past verbs and short participles only: «задержанные», «к задержанным» are people.
    (EventType.DETENTION, _W + r"(?:задерж(?:ал|али|ала|ало|ивали|ан|ана|аны|ано)(?![а-яё]))"),
    (
        EventType.ARREST,
        _W + r"(?:арестова\w*|(?:заключ|взя|помести)\w*\s+под\s+страж\w*)",
    ),
    (
        EventType.CHARGE,
        _W + r"(?:обвинил\w*|обвиня\w*|предъяв\w*\s+обвинени\w*|стал\w*\s+обвиняем\w*)",
    ),
    (EventType.SENTENCE, _W + r"(?:приговорил\w*|осудил\w*|осужден(?:а|ы|о)?(?![а-яё]))"),
    (EventType.FINE, _W + r"(?:оштрафова\w*)"),
    (EventType.RELEASE, _W + r"(?:освобо[дж]\w*|отпустил\w*|вышел\w*\s+на\s+свободу)"),
)
_NOUN_TRIGGERS: tuple[tuple[EventType, str], ...] = (
    (EventType.CASE_OPENED, _W + r"(?:уголовн\w*\s+дел\w*)"),
    (EventType.ARREST, _W + r"(?:арест(?:а|е|ом|у)?)(?![а-яё])"),
    (EventType.SENTENCE, _W + r"(?:приговор(?:а|е|ом|у)?)(?![а-яё])"),
    (EventType.FINE, _W + r"(?:штраф\w*)"),
)
_NEGATION_BEFORE = re.compile(r"(?<![а-яё])не\s+(?:[а-яё]+\s+)?$")
# «до ареста», «после приговора», «перед задержанием»: a reference to another event.
_TEMPORAL_REFERENCE_BEFORE = re.compile(
    r"(?<![а-яё])(?:до|после|перед|с\s+момента|во\s+время)\s+(?:его\s+|ее\s+|её\s+|их\s+)?$"
)


class RuleBasedEventExtractor:
    extractor_name = "rule-based-event-extractor"
    extractor_version = "1.2.0"

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
            trigger = self._trigger(lowered)
            if trigger is None:
                continue
            event_type, trigger_text = trigger
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
                    attributes={"trigger_text": trigger_text},
                    links=links,
                    extractor_name=self.extractor_name,
                    extractor_version=self.extractor_version,
                )
            )
        return sorted(events, key=lambda event: (event.start_offset, event.event_type.value))

    @staticmethod
    def _trigger(lowered_sentence: str) -> tuple[EventType, str] | None:
        """The earliest non-negated verb trigger, else the earliest noun trigger."""
        for triggers in (_VERB_TRIGGERS, _NOUN_TRIGGERS):
            found: list[tuple[int, EventType, str]] = []
            for event_type, pattern in triggers:
                for match in re.finditer(pattern, lowered_sentence):
                    prefix = lowered_sentence[: match.start()]
                    if _NEGATION_BEFORE.search(prefix):
                        continue
                    if triggers is _NOUN_TRIGGERS and _TEMPORAL_REFERENCE_BEFORE.search(prefix):
                        continue
                    found.append((match.start(), event_type, match.group(0)))
                    break
            if found:
                _, event_type, text = min(found, key=lambda item: item[0])
                return event_type, text
        return None

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
