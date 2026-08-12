"""Service for searching court cases by characteristics extracted from press releases.

Implements case search by:
- Article (UK RF)
- Date (decision/received)
- Court
- Person name (if available)

Returns candidates with explainable matching reasons.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from sqlalchemy import ColumnElement, and_, or_, select
from sqlalchemy.orm import Session

from court_monitor.domain.models import DATE_TOLERANCE_DAYS
from court_monitor.storage.orm import Case, PersonCase, PersonRecord


@dataclass
class CaseSearchCriteria:
    """Criteria for searching cases."""

    court: str | None = None
    article: str | None = None
    decision_date: date | None = None
    received_date: date | None = None
    person_name: str | None = None
    date_tolerance_days: int = 7  # Tolerance for date matching


@dataclass
class CaseMatchReason:
    """A single reason why a case matches the criteria."""

    field_name: str
    reason: str
    criteria_value: str
    case_value: str
    weight: float


@dataclass
class CaseCandidate:
    """A candidate case with matching score and reasons."""

    case: Case
    score: float
    reasons: list[CaseMatchReason] = field(default_factory=list)
    missing_fields: list[str] = field(default_factory=list)


# Scoring weights (aligned with case_matching.py)
W_ARTICLE_MATCH = 0.35
W_DATE_MATCH = 0.30
W_COURT_MATCH = 0.20
W_PERSON_NAME_MATCH = 0.15

# Minimum confidence threshold for candidates
# Set to the lowest weight (person_name) so single-signal matches are shown to operator
MIN_CONFIDENCE_THRESHOLD = 0.15


def search_cases(
    session: Session,
    criteria: CaseSearchCriteria,
    limit: int = 20,
) -> list[CaseCandidate]:
    """Search for cases matching the criteria.

    Returns candidates sorted by score (descending).
    """
    # Build query with OR conditions for flexible matching
    conditions: list[ColumnElement[bool]] = []

    if criteria.court:
        conditions.append(Case.court.ilike(f"%{criteria.court}%"))

    if criteria.article:
        # Search in PersonCase articles
        conditions.append(
            Case.id.in_(
                select(PersonCase.case_id).where(PersonCase.articles.ilike(f"%{criteria.article}%"))
            )
        )

    if criteria.decision_date:
        # decision_date matches only decision_at (sentence/decision date)
        date_min = criteria.decision_date - timedelta(days=DATE_TOLERANCE_DAYS)
        date_max = criteria.decision_date + timedelta(days=DATE_TOLERANCE_DAYS)
        conditions.append(
            and_(Case.decision_at.isnot(None), Case.decision_at.between(date_min, date_max))
        )

    if criteria.received_date:
        # received_date matches only received_at (case receipt date)
        date_min = criteria.received_date - timedelta(days=DATE_TOLERANCE_DAYS)
        date_max = criteria.received_date + timedelta(days=DATE_TOLERANCE_DAYS)
        conditions.append(
            and_(Case.received_at.isnot(None), Case.received_at.between(date_min, date_max))
        )

    if criteria.person_name:
        # Search in PersonRecord via PersonCase
        conditions.append(
            Case.id.in_(
                select(PersonCase.case_id)
                .join(PersonRecord, PersonCase.person_id == PersonRecord.id)
                .where(
                    or_(
                        PersonRecord.normalized_name.ilike(f"%{criteria.person_name}%"),
                        PersonRecord.raw_name.ilike(f"%{criteria.person_name}%"),
                    )
                )
            )
        )

    if not conditions:
        return []

    # Query cases matching any condition
    stmt = select(Case).where(or_(*conditions)).limit(limit * 2)  # Fetch more for scoring
    cases = session.execute(stmt).scalars().all()

    # Load PersonCase for each case
    case_ids = [c.id for c in cases]
    person_cases_by_case_id: dict[int, list[PersonCase]] = {}
    if case_ids:
        pc_stmt = select(PersonCase).where(PersonCase.case_id.in_(case_ids))
        person_cases = session.execute(pc_stmt).scalars().all()
        for pc in person_cases:
            if pc.case_id not in person_cases_by_case_id:
                person_cases_by_case_id[pc.case_id] = []
            person_cases_by_case_id[pc.case_id].append(pc)

    # Score each case
    candidates = []
    for case in cases:
        person_cases = person_cases_by_case_id.get(case.id, [])
        candidate = score_case_match(session, case, criteria, person_cases)
        if candidate.score >= MIN_CONFIDENCE_THRESHOLD:
            candidates.append(candidate)

    # Sort by score and limit
    candidates.sort(key=lambda c: c.score, reverse=True)
    return candidates[:limit]


def score_case_match(
    session: Session,
    case: Case,
    criteria: CaseSearchCriteria,
    person_cases: list[PersonCase] | None = None,
) -> CaseCandidate:
    """Score how well a case matches the criteria.

    Returns a CaseCandidate with score and explainable reasons.
    """
    reasons: list[CaseMatchReason] = []
    missing_fields: list[str] = []
    total_score = 0.0

    # Article match
    if criteria.article:
        article_match = _check_article_match(case, criteria.article, person_cases or [])
        if article_match:
            reasons.append(
                CaseMatchReason(
                    field_name="article",
                    reason="Статья УК совпадает",
                    criteria_value=criteria.article,
                    case_value=article_match,
                    weight=W_ARTICLE_MATCH,
                )
            )
            total_score += W_ARTICLE_MATCH
        else:
            missing_fields.append("article")

    # Date match
    if criteria.decision_date or criteria.received_date:
        date_match = _check_date_match(case, criteria)
        if date_match:
            reasons.append(
                CaseMatchReason(
                    field_name="date",
                    reason="Дата совпадает (с учётом допуска)",
                    criteria_value=str(criteria.decision_date or criteria.received_date),
                    case_value=date_match,
                    weight=W_DATE_MATCH,
                )
            )
            total_score += W_DATE_MATCH
        else:
            missing_fields.append("date")

    # Court match
    if criteria.court:
        court_match = _check_court_match(case, criteria.court)
        if court_match:
            reasons.append(
                CaseMatchReason(
                    field_name="court",
                    reason="Суд совпадает",
                    criteria_value=criteria.court,
                    case_value=court_match,
                    weight=W_COURT_MATCH,
                )
            )
            total_score += W_COURT_MATCH
        else:
            missing_fields.append("court")

    # Person name match (if available)
    if criteria.person_name:
        name_match = _check_person_name_match(
            session, case, criteria.person_name, person_cases or []
        )
        if name_match:
            reasons.append(
                CaseMatchReason(
                    field_name="person_name",
                    reason="ФИО совпадает",
                    criteria_value=criteria.person_name,
                    case_value=name_match,
                    weight=W_PERSON_NAME_MATCH,
                )
            )
            total_score += W_PERSON_NAME_MATCH
        else:
            missing_fields.append("person_name")

    return CaseCandidate(
        case=case,
        score=total_score,
        reasons=reasons,
        missing_fields=missing_fields,
    )


def _check_article_match(case: Case, article: str, person_cases: list[PersonCase]) -> str | None:
    """Check if case has matching article.

    Returns the matched article string or None.
    """
    # Normalize article for comparison
    article_normalized = re.sub(r"[^\d.]", "", article)

    for pc in person_cases:
        if pc.articles:
            # Extract article numbers from the articles string
            # Match patterns like "205.1" or "205" but ensure exact match
            case_articles = re.findall(r"\d+(?:\.\d+)?", pc.articles)
            if article_normalized in case_articles:
                return pc.articles

    return None


def _check_date_match(case: Case, criteria: CaseSearchCriteria) -> str | None:
    """Check if case date matches criteria (with tolerance).

    Strict semantics:
    - decision_date matches ONLY decision_at (sentence/decision date)
    - received_date matches ONLY received_at (case receipt date)

    Never cross-match: decision_date with received_at or vice versa.
    These are different events in the case lifecycle.

    The hasattr check is defence-in-depth — after migration 0014, columns are
    DateTime, but if someone runs an old migration or creates columns manually
    as Date, the code won't crash.

    Returns the matched date string or None.
    """
    # Check decision_date against decision_at only
    if criteria.decision_date and case.decision_at:
        case_date_raw = case.decision_at
        case_date: date = case_date_raw.date() if hasattr(case_date_raw, "date") else case_date_raw
        diff = abs((case_date - criteria.decision_date).days)
        if diff <= DATE_TOLERANCE_DAYS:
            return case_date.isoformat()

    # Check received_date against received_at only
    if criteria.received_date and case.received_at:
        case_date_raw = case.received_at
        case_date = case_date_raw.date() if hasattr(case_date_raw, "date") else case_date_raw
        diff = abs((case_date - criteria.received_date).days)
        if diff <= DATE_TOLERANCE_DAYS:
            return case_date.isoformat()

    return None


def _check_court_match(case: Case, court: str) -> str | None:
    """Check if case court matches criteria.

    Returns the matched court string or None.
    """
    if not case.court:
        return None

    # Case-insensitive substring match
    if court.lower() in case.court.lower():
        return case.court

    return None


def _check_person_name_match(
    session: Session, case: Case, person_name: str, person_cases: list[PersonCase]
) -> str | None:
    """Check if case has matching person name.

    Returns the matched name string or None.
    Joins with PersonRecord to get the actual person name.
    """
    if not person_cases:
        return None

    # Get person_ids from person_cases
    person_ids = [pc.person_id for pc in person_cases if pc.person_id]
    if not person_ids:
        return None

    # Query PersonRecord for these IDs
    stmt = select(PersonRecord).where(PersonRecord.id.in_(person_ids))
    person_records = session.execute(stmt).scalars().all()

    # Normalize input name for comparison
    person_name_normalized = " ".join(person_name.lower().split())

    for pr in person_records:
        # Compare with normalized_name or raw_name
        for name_field in [pr.normalized_name, pr.raw_name]:
            if name_field:
                name_normalized = " ".join(name_field.lower().split())
                if person_name_normalized == name_normalized:
                    return name_field
                # Check partial match (surname + name)
                if _names_match_partially(person_name_normalized, name_normalized):
                    return name_field

    return None


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


def format_case_candidate_summary(candidate: CaseCandidate) -> dict[str, Any]:
    """Format a case candidate for display/review.

    Returns a dict with case info and matching explanation.
    """
    return {
        "case_id": candidate.case.id,
        "case_number": candidate.case.case_number,
        "court": candidate.case.court,
        "score": candidate.score,
        "reasons": [
            {
                "field": r.field_name,
                "reason": r.reason,
                "criteria_value": r.criteria_value,
                "case_value": r.case_value,
                "weight": r.weight,
            }
            for r in candidate.reasons
        ],
        "missing_fields": candidate.missing_fields,
        "source_url": candidate.case.source_url,
    }
