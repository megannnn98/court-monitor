"""Event classification for press releases and case card events."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum


class PressEventType(StrEnum):
    sentence_delivered = "sentence_delivered"
    preventive_measure_selected = "preventive_measure_selected"
    hearing_scheduled = "hearing_scheduled"
    appeal_decided = "appeal_decided"
    case_received = "case_received"
    unknown = "unknown"


class CaseEventType(StrEnum):
    sentence_delivered = "sentence_delivered"
    preventive_measure = "preventive_measure"
    hearing = "hearing"
    appeal_filed = "appeal_filed"
    appeal_decided = "appeal_decided"
    sentence_overturned = "sentence_overturned"
    sentence_modified = "sentence_modified"
    case_transfer = "case_transfer"
    unknown = "unknown"


@dataclass(frozen=True)
class EventClassification:
    event_type: PressEventType | CaseEventType
    confidence: float
    quote: str
    method: str


_SENTENCE_PATTERNS = (
    (re.compile(r"вынесен(?:о)?\s+приговор", re.IGNORECASE), 0.95),
    (re.compile(r"вынес(?:ла|ли)?\s+приговор", re.IGNORECASE), 0.95),
    (re.compile(r"приговорил", re.IGNORECASE), 0.90),
    (re.compile(r"осужден(?:а|ы|о)?", re.IGNORECASE), 0.85),
    (re.compile(r"осудил", re.IGNORECASE), 0.85),
    (re.compile(r"назначил наказание", re.IGNORECASE), 0.85),
)

# "вынес постановление" без контекста — НЕ sentence. Only when followed by
# "приговор" / "обвинительный" does it become sentence_delivered. Otherwise
# it will be matched by _PREVENTIVE_PATTERNS or _HEARING_PATTERNS if they
# appear nearby.
_SENTENCE_POSTANOVLENIE_CONTEXT = (
    re.compile(r"вынес\s+постановление\s+(?:об\s+)?(?:обвинительном\s+)?приговор", re.IGNORECASE),
)

_PREVENTIVE_PATTERNS = (
    (re.compile(r"избран[аоы]?\s+мера\s+пресечения", re.IGNORECASE), 0.95),
    (re.compile(r"избрание\s+меры\s+пресечения", re.IGNORECASE), 0.95),
    (re.compile(r"заключен(?:а)?\s+под\s+стражу", re.IGNORECASE), 0.95),
    (re.compile(r"заключение\s+под\s+стражу", re.IGNORECASE), 0.95),
    (re.compile(r"продлен\s+срок\s+содержания\s+под\s+стражей", re.IGNORECASE), 0.95),
    (re.compile(r"постановление\s+(?:об\s+)?избрании\s+меры\s+пресечения", re.IGNORECASE), 0.95),
    (re.compile(r"мер[аы]\s+пресечения", re.IGNORECASE), 0.80),
)

_HEARING_PATTERNS = (
    (re.compile(r"назначено (судебное )?заседание", re.IGNORECASE), 0.90),
    (re.compile(r"назначено слушание", re.IGNORECASE), 0.85),
)

_APPEAL_PATTERNS = (
    (re.compile(r"рассмотрена апелляционная жалоба", re.IGNORECASE), 0.95),
    (re.compile(r"рассмотрена жалоба", re.IGNORECASE), 0.80),
)

_CASE_RECEIVED_PATTERNS = ((re.compile(r"поступило уголовное дело", re.IGNORECASE), 0.95),)


def classify_press_event(text: str) -> EventClassification:  # noqa: PLR0911
    """Classify the type of court event described in a press release text.

    The order matters:
      1. sentence patterns that explicitly state a verdict ("вынесен
         приговор", "приговорил к...", "осудил").
      2. preventive-measure patterns — matched BEFORE "вынес постановление"
         because a "постановление об избрании меры пресечения" is NOT a
         sentence (task §7).
      3. "вынес постановление" only counts as a sentence when followed by
         "приговор"/"обвинительный приговор".
      4. hearing patterns ("назначено судебное заседание").
      5. appeal patterns.
      6. case-received patterns.

    If nothing matches, return ``unknown`` — the caller treats this as
    "no confirmed decision date available".
    """
    for pattern, confidence in _SENTENCE_PATTERNS:
        m = pattern.search(text)
        if m:
            return EventClassification(
                PressEventType.sentence_delivered, confidence, m.group(0), "regex"
            )

    for pattern, confidence in _PREVENTIVE_PATTERNS:
        m = pattern.search(text)
        if m:
            return EventClassification(
                PressEventType.preventive_measure_selected, confidence, m.group(0), "regex"
            )

    for pattern in _SENTENCE_POSTANOVLENIE_CONTEXT:
        m = pattern.search(text)
        if m:
            return EventClassification(PressEventType.sentence_delivered, 0.80, m.group(0), "regex")

    for pattern, confidence in _HEARING_PATTERNS:
        m = pattern.search(text)
        if m:
            return EventClassification(
                PressEventType.hearing_scheduled, confidence, m.group(0), "regex"
            )
    for pattern, confidence in _APPEAL_PATTERNS:
        m = pattern.search(text)
        if m:
            return EventClassification(
                PressEventType.appeal_decided, confidence, m.group(0), "regex"
            )
    for pattern, confidence in _CASE_RECEIVED_PATTERNS:
        m = pattern.search(text)
        if m:
            return EventClassification(
                PressEventType.case_received, confidence, m.group(0), "regex"
            )
    return EventClassification(PressEventType.unknown, 0.0, "", "regex")


# ── case card event classification ──

_SENTENCE_EVENT_PATTERNS = (
    re.compile(r"вынесен(?:ие|ы|о)?\s+приговор", re.IGNORECASE),
    re.compile(r"вынес(?:ла|ли)?\s+приговор", re.IGNORECASE),
    re.compile(r"^приговор", re.IGNORECASE),
)

_APPEAL_FILED_PATTERNS = (
    re.compile(r"обжалован(?:ие|о)?\s+приговор", re.IGNORECASE),
    re.compile(r"подана\s+апелляцион", re.IGNORECASE),
    re.compile(r"поступила\s+апелляционн", re.IGNORECASE),
    re.compile(r"пода[лпя]\s+апелляционн", re.IGNORECASE),
)

_OVERTURNED_PATTERNS = (
    re.compile(r"отменен[аоы]?\s+приговор", re.IGNORECASE),
    re.compile(r"отмен[аеяи]?\s+приговор", re.IGNORECASE),
    re.compile(r"отменен[аоы]?\s+(?:постановление|определение)", re.IGNORECASE),
    re.compile(r"отмен[аеяи]?\s+(?:постановление|определение)", re.IGNORECASE),
)

_MODIFIED_PATTERNS = (re.compile(r"изменен(ие|о) приговор", re.IGNORECASE),)


def classify_case_event(event_type: str, result: str | None = None) -> EventClassification:  # noqa: PLR0911
    """Classify a case card event by event_type and optional result."""
    combined = f"{event_type or ''} {result or ''}"

    for pattern in _SENTENCE_EVENT_PATTERNS:
        if pattern.search(combined):
            return EventClassification(
                CaseEventType.sentence_delivered, 0.90, combined.strip(), "regex"
            )

    for pattern in _OVERTURNED_PATTERNS:
        if pattern.search(combined):
            return EventClassification(
                CaseEventType.sentence_overturned, 0.90, combined.strip(), "regex"
            )

    for pattern in _MODIFIED_PATTERNS:
        if pattern.search(combined):
            return EventClassification(
                CaseEventType.sentence_modified, 0.85, combined.strip(), "regex"
            )

    for pattern in _APPEAL_FILED_PATTERNS:
        if pattern.search(combined):
            return EventClassification(CaseEventType.appeal_filed, 0.85, combined.strip(), "regex")

    if "апелляцион" in combined.lower():
        return EventClassification(CaseEventType.appeal_decided, 0.70, combined.strip(), "regex")

    if any(kw in combined.lower() for kw in ("заседание", "слушание")):
        return EventClassification(CaseEventType.hearing, 0.70, combined.strip(), "regex")

    if any(
        kw in combined.lower()
        for kw in ("мера пресечения", "меры пресечения", "содержан", "заключен", "арест")
    ) or re.search(r"мер[аы]\s+пресечени[яе]", combined, re.IGNORECASE):
        return EventClassification(
            CaseEventType.preventive_measure, 0.80, combined.strip(), "regex"
        )

    if "переда" in combined.lower() and "дела" in combined.lower():
        return EventClassification(CaseEventType.case_transfer, 0.75, combined.strip(), "regex")

    return EventClassification(CaseEventType.unknown, 0.0, combined.strip(), "regex")
