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
    (re.compile(r"вынесен приговор", re.IGNORECASE), 0.95),
    (re.compile(r"приговорил", re.IGNORECASE), 0.90),
    (re.compile(r"осудил", re.IGNORECASE), 0.85),
    (re.compile(r"назначил наказание", re.IGNORECASE), 0.85),
    (re.compile(r"вынес постановление", re.IGNORECASE), 0.80),
)

_PREVENTIVE_PATTERNS = (
    (re.compile(r"избрана мера пресечения", re.IGNORECASE), 0.95),
    (re.compile(r"заключен под стражу", re.IGNORECASE), 0.95),
    (re.compile(r"продлен срок содержания под стражей", re.IGNORECASE), 0.95),
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


def classify_press_event(text: str) -> EventClassification:
    """Classify the type of court event described in a press release text."""
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
    re.compile(r"вынесен(ие|о) приговор", re.IGNORECASE),
    re.compile(r"^приговор", re.IGNORECASE),
)

_APPEAL_FILED_PATTERNS = (
    re.compile(r"обжалован(ие|о) приговор", re.IGNORECASE),
    re.compile(r"подана апелляцион", re.IGNORECASE),
)

_OVERTURNED_PATTERNS = (
    re.compile(r"отмен(а|ен) приговор", re.IGNORECASE),
    re.compile(r"отмен(а|ено) постановление", re.IGNORECASE),
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

    if any(kw in combined.lower() for kw in ("мера пресечения", "содержан", "заключен", "арест")):
        return EventClassification(
            CaseEventType.preventive_measure, 0.80, combined.strip(), "regex"
        )

    if "переда" in combined.lower() and "дела" in combined.lower():
        return EventClassification(CaseEventType.case_transfer, 0.75, combined.strip(), "regex")

    return EventClassification(CaseEventType.unknown, 0.0, combined.strip(), "regex")
