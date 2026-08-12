"""Explainable matching for linking press releases to court cases.

Implements the matching logic described in the task:
- Match by article (UK RF)
- Match by date (decision/received)
- Match by court
- Match by person name (if available in case card)

Each match produces explainable signals and missing fields.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from court_monitor.domain.models import DATE_TOLERANCE_DAYS
from court_monitor.parsers.sud_delo import ParsedCaseCard


@dataclass
class MatchSignal:
    """A positive signal that supports a match."""

    signal_type: str
    description: str
    criteria_value: str
    case_value: str
    weight: float


@dataclass
class MatchMissing:
    """A field that is missing or cannot be verified."""

    field_name: str
    description: str


@dataclass
class CaseMatchResult:
    """Result of matching a press release to a case card."""

    case_card: ParsedCaseCard
    confidence: float
    signals: list[MatchSignal] = field(default_factory=list)
    missing: list[MatchMissing] = field(default_factory=list)
    conflicts: list[dict[str, Any]] = field(default_factory=list)


# Weights for different signals
W_ARTICLE_MATCH = 0.35
W_DATE_MATCH = 0.30
W_COURT_MATCH = 0.20
W_PERSON_NAME_MATCH = 0.15


def match_press_release_to_case(
    *,
    article: str | None,
    decision_date: date | None,
    court: str | None,
    person_name: str | None,
    case_card: ParsedCaseCard,
) -> CaseMatchResult:
    """Match a press release to a case card.

    Args:
        article: Article from press release (e.g., "205.1")
        decision_date: Decision date from press release
        court: Court name from press release
        person_name: Person name from press release (may be None if hidden)
        case_card: Parsed case card from sud_delo

    Returns:
        CaseMatchResult with confidence score and explainable signals
    """
    signals: list[MatchSignal] = []
    missing: list[MatchMissing] = []
    conflicts: list[dict[str, Any]] = []
    total_score = 0.0

    # 1. Article match
    article_score, article_signals, article_missing = _score_article(article, case_card)
    total_score += article_score
    signals.extend(article_signals)
    missing.extend(article_missing)

    # 2. Date match
    date_score, date_signals, date_missing = _score_date(decision_date, case_card)
    total_score += date_score
    signals.extend(date_signals)
    missing.extend(date_missing)

    # 3. Court match
    court_score, court_signals, court_missing = _score_court(court, case_card)
    total_score += court_score
    signals.extend(court_signals)
    missing.extend(court_missing)

    # 4. Person name match (if available)
    name_score, name_signals, name_missing, name_conflicts = _score_person_name(
        person_name, case_card
    )
    total_score += name_score
    signals.extend(name_signals)
    missing.extend(name_missing)
    conflicts.extend(name_conflicts)

    return CaseMatchResult(
        case_card=case_card,
        confidence=total_score,
        signals=signals,
        missing=missing,
        conflicts=conflicts,
    )


def _score_article(
    article: str | None, case_card: ParsedCaseCard
) -> tuple[float, list[MatchSignal], list[MatchMissing]]:
    """Score article match."""
    if not article:
        return 0.0, [], []

    article_match = _check_article_match(article, case_card)
    if article_match:
        return (
            W_ARTICLE_MATCH,
            [
                MatchSignal(
                    signal_type="article",
                    description=f"Совпадает статья {article}",
                    criteria_value=article,
                    case_value=article_match,
                    weight=W_ARTICLE_MATCH,
                )
            ],
            [],
        )

    return (
        0.0,
        [],
        [
            MatchMissing(
                field_name="article",
                description=f"Статья {article} не найдена в карточке дела",
            )
        ],
    )


def _score_date(
    decision_date: date | None, case_card: ParsedCaseCard
) -> tuple[float, list[MatchSignal], list[MatchMissing]]:
    """Score date match."""
    if not decision_date:
        return 0.0, [], []

    date_match = _check_date_match(decision_date, case_card)
    if date_match:
        return (
            W_DATE_MATCH,
            [
                MatchSignal(
                    signal_type="date",
                    description=f"Совпадает дата решения {decision_date}",
                    criteria_value=str(decision_date),
                    case_value=date_match,
                    weight=W_DATE_MATCH,
                )
            ],
            [],
        )

    return (
        0.0,
        [],
        [
            MatchMissing(
                field_name="date",
                description=f"Дата {decision_date} не совпадает с карточкой дела",
            )
        ],
    )


def _score_court(
    court: str | None, case_card: ParsedCaseCard
) -> tuple[float, list[MatchSignal], list[MatchMissing]]:
    """Score court match."""
    if not court:
        return 0.0, [], []

    court_match = _check_court_match(court, case_card)
    if court_match:
        return (
            W_COURT_MATCH,
            [
                MatchSignal(
                    signal_type="court",
                    description=f"Совпадает суд {court}",
                    criteria_value=court,
                    case_value=court_match,
                    weight=W_COURT_MATCH,
                )
            ],
            [],
        )

    return (
        0.0,
        [],
        [
            MatchMissing(
                field_name="court",
                description=f"Суд {court} не совпадает с карточкой дела",
            )
        ],
    )


def _score_person_name(
    person_name: str | None, case_card: ParsedCaseCard
) -> tuple[float, list[MatchSignal], list[MatchMissing], list[dict[str, Any]]]:
    """Score person name match."""
    if not person_name:
        return 0.0, [], [], []

    name_match = _check_person_name_match(person_name, case_card)
    if name_match:
        return (
            W_PERSON_NAME_MATCH,
            [
                MatchSignal(
                    signal_type="person_name",
                    description=f"Совпадает ФИО {person_name}",
                    criteria_value=person_name,
                    case_value=name_match,
                    weight=W_PERSON_NAME_MATCH,
                )
            ],
            [],
            [],
        )

    if _is_person_hidden(case_card):
        return (
            0.0,
            [],
            [
                MatchMissing(
                    field_name="person_name",
                    description="ФИО скрыто в карточке дела",
                )
            ],
            [],
        )

    return (
        0.0,
        [],
        [],
        [
            {
                "field": "person_name",
                "criteria_value": person_name,
                "case_value": "не найдено",
                "description": "ФИО из пресс-релиза не найдено в карточке дела",
            }
        ],
    )


def _check_article_match(article: str, case_card: ParsedCaseCard) -> str | None:
    """Check if article matches any article in case card.

    Returns the matched article string or None.
    """
    if not case_card.persons:
        return None

    # Normalize article for comparison (e.g., "205.1" -> "205.1")
    article_normalized = _normalize_article(article)

    for person in case_card.persons:
        for case_article in person.articles:
            case_article_normalized = _normalize_article(case_article)
            if article_normalized == case_article_normalized:
                return case_article

    return None


def _normalize_article(article: str) -> str:
    """Normalize article for comparison.

    Extracts the main article number (e.g., "ст.205.1 ч.1 УК РФ" -> "205.1").
    """
    # Extract article number with proper boundaries
    # Match "ст.205.1" or just "205.1" but not "205.1.1" (which would be part + article)
    match = re.search(r"ст\.?\s*(\d+(?:\.\d+)?)", article)
    if match:
        return match.group(1)
    # Fallback: extract just the number
    match = re.search(r"(\d+(?:\.\d+)?)", article)
    if match:
        return match.group(1)
    return article.strip()


def _check_date_match(decision_date: date, case_card: ParsedCaseCard) -> str | None:
    """Check if decision_date matches case card events.

    Strict semantics: decision_date matches only sentence/decision events,
    never received_at (case receipt date is a different event).

    Returns the matched date string or None.
    """
    # Check events for decision/sentence date
    for event in case_card.events:
        if event.event_date and _is_decision_event(event.event_type):
            diff = abs((event.event_date - decision_date).days)
            if diff <= DATE_TOLERANCE_DAYS:
                return event.event_date.isoformat()

    return None


def _is_decision_event(event_type: str) -> bool:
    """Check if event type indicates a decision/sentence."""
    event_lower = event_type.lower()
    return any(
        keyword in event_lower
        for keyword in [
            "приговор",
            "решение",
            "постановление",
            "определение",
        ]
    )


def _check_court_match(court: str, case_card: ParsedCaseCard) -> str | None:
    """Check if court matches case card court.

    Returns the matched court string or None.
    """
    # Case card doesn't have court field directly, but we can check first_instance_court
    if case_card.first_instance_court and court.lower() in case_card.first_instance_court.lower():
        return case_card.first_instance_court

    return None


def _check_person_name_match(person_name: str, case_card: ParsedCaseCard) -> str | None:
    """Check if person name matches any person in case card.

    Returns the matched name string or None.
    """
    if not case_card.persons:
        return None

    # Normalize name for comparison
    person_name_normalized = _normalize_name(person_name)

    for person in case_card.persons:
        person_normalized = _normalize_name(person.name)
        if person_name_normalized == person_normalized:
            return person.name

        # Check for partial match (surname + name)
        if _names_match_partially(person_name_normalized, person_normalized):
            return person.name

    return None


def _normalize_name(name: str) -> str:
    """Normalize name for comparison.

    Converts to lowercase and removes extra spaces.
    """
    return " ".join(name.lower().split())


def _names_match_partially(name1: str, name2: str) -> bool:
    """Check if two names match partially (surname + name).

    Handles cases where patronymic may be missing.
    """
    parts1 = name1.split()
    parts2 = name2.split()

    if len(parts1) < 2 or len(parts2) < 2:
        return False

    # Compare surname and name
    return parts1[0] == parts2[0] and parts1[1] == parts2[1]


# Patterns for hidden person information in case cards
_HIDDEN_NAME_PATTERNS = (
    "информация скрыта",
    "данные скрыты",
    "сведения скрыты",
    "информация отсутствует",
    "данные отсутствуют",
)


def _is_person_hidden(case_card: ParsedCaseCard) -> bool:
    """Check if person info is hidden in case card."""
    if not case_card.persons:
        return True

    return any(
        any(pattern in person.name.lower() for pattern in _HIDDEN_NAME_PATTERNS)
        for person in case_card.persons
    )


def format_match_result(result: CaseMatchResult) -> dict[str, Any]:
    """Format match result for display/review.

    Returns a dict with match explanation.
    """
    return {
        "case_number": result.case_card.case_number,
        "case_uid": result.case_card.case_uid,
        "confidence": result.confidence,
        "signals": [
            {
                "type": s.signal_type,
                "description": s.description,
                "criteria_value": s.criteria_value,
                "case_value": s.case_value,
                "weight": s.weight,
            }
            for s in result.signals
        ],
        "missing": [
            {
                "field": m.field_name,
                "description": m.description,
            }
            for m in result.missing
        ],
        "conflicts": result.conflicts,
    }
