"""Deterministic, explainable scoring for person match candidates.

All weights are module-level constants — easy to tune without touching logic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from court_monitor.matching.name_normalizer import NormalizedName

# ---------------------------------------------------------------------------
# Scoring weights
# ---------------------------------------------------------------------------

W_FULL_NAME_MATCH = 0.70
W_SURNAME_NAME_MATCH = 0.50
W_SURNAME_INITIALS_MATCH = 0.35
W_BIRTH_DATE_MATCH = 0.20
W_BIRTHPLACE_MATCH = 0.10

P_BIRTH_DATE_CONFLICT = -0.50
P_NAME_CONFLICT = -0.40

# Threshold for creating a candidate
CANDIDATE_THRESHOLD = 0.45

ALGORITHM_VERSION = "match-v1"


@dataclass
class ScoreResult:
    """Detailed scoring breakdown."""

    score: float
    name_score: float
    birth_date_score: float
    birthplace_score: float
    reasons: list[dict[str, Any]] = field(default_factory=list)
    conflicts: list[dict[str, Any]] = field(default_factory=list)


def score_match(
    doc_name: NormalizedName,
    record_name: NormalizedName,
    doc_birth_date: str | None,
    record_birth_date: str | None,
    *,
    doc_birth_place: str | None = None,
    record_birth_place: str | None = None,
) -> ScoreResult:
    """Score a potential match between a document person and a registry record.

    Returns a ScoreResult with detailed reasons and conflicts.
    """
    reasons: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []

    name_score = _score_name(doc_name, record_name, reasons, conflicts)
    birth_date_score = _score_birth_date(
        doc_birth_date, record_birth_date, reasons, conflicts
    )
    birthplace_score = _score_birthplace(
        doc_birth_place, record_birth_place, reasons, conflicts
    )

    total = max(0.0, min(1.0, name_score + birth_date_score + birthplace_score))

    return ScoreResult(
        score=total,
        name_score=name_score,
        birth_date_score=birth_date_score,
        birthplace_score=birthplace_score,
        reasons=reasons,
        conflicts=conflicts,
    )


def _score_name(
    doc: NormalizedName,
    rec: NormalizedName,
    reasons: list[dict],
    conflicts: list[dict],
) -> float:
    """Score name similarity."""
    if not doc.surname or not rec.surname:
        reasons.append({"rule": "name_data_missing", "impact": 0.0})
        return 0.0

    surname_match = doc.surname == rec.surname
    name_match = doc.name == rec.name if (doc.name and rec.name) else False
    patronymic_match = doc.patronymic == rec.patronymic if (doc.patronymic and rec.patronymic) else False

    # Check for conflict: different surname is a strong negative signal
    if not surname_match:
        conflicts.append({
            "rule": "surname_mismatch",
            "document_value": doc.surname,
            "record_value": rec.surname,
        })
        return 0.0

    # Full name match
    if surname_match and name_match and patronymic_match:
        reasons.append({
            "rule": "full_name_after_normalization",
            "impact": W_FULL_NAME_MATCH,
            "document_value": doc.raw,
            "record_value": rec.raw,
        })
        return W_FULL_NAME_MATCH

    # Surname + name match (no patronymic data or mismatch)
    if surname_match and name_match:
        impact = W_SURNAME_NAME_MATCH
        if doc.patronymic and rec.patronymic and not patronymic_match:
            conflicts.append({
                "rule": "patronymic_mismatch",
                "document_value": doc.patronymic,
                "record_value": rec.patronymic,
            })
            impact += P_NAME_CONFLICT
        reasons.append({
            "rule": "surname_name_match",
            "impact": impact,
            "document_value": f"{doc.surname} {doc.name}",
            "record_value": f"{rec.surname} {rec.name}",
        })
        return max(0.0, impact)

    # Surname + initials match
    if surname_match and doc.initials and rec.initials:
        # Compare initials (first letter of name + patronymic)
        doc_init = doc.initials[1:] if len(doc.initials) > 1 else ""
        rec_init = rec.initials[1:] if len(rec.initials) > 1 else ""
        if doc_init and rec_init and doc_init == rec_init:
            reasons.append({
                "rule": "surname_initials_match",
                "impact": W_SURNAME_INITIALS_MATCH,
                "document_value": f"{doc.surname} {doc.initials}",
                "record_value": f"{rec.surname} {rec.initials}",
            })
            return W_SURNAME_INITIALS_MATCH

    # Surname only
    reasons.append({
        "rule": "surname_only_match",
        "impact": W_SURNAME_INITIALS_MATCH * 0.5,
        "document_value": doc.surname,
        "record_value": rec.surname,
    })
    return W_SURNAME_INITIALS_MATCH * 0.5


def _score_birth_date(
    doc_date: str | None,
    rec_date: str | None,
    reasons: list[dict],
    conflicts: list[dict],
) -> float:
    """Score birth date similarity."""
    if not doc_date and not rec_date:
        reasons.append({"rule": "birth_date_missing_both", "impact": 0.0})
        return 0.0

    if not doc_date:
        reasons.append({"rule": "birth_date_missing_in_document", "impact": 0.0})
        return 0.0

    if not rec_date:
        reasons.append({"rule": "birth_date_missing_in_record", "impact": 0.0})
        return 0.0

    if doc_date == rec_date:
        reasons.append({
            "rule": "birth_date_match",
            "impact": W_BIRTH_DATE_MATCH,
            "document_value": doc_date,
            "record_value": rec_date,
        })
        return W_BIRTH_DATE_MATCH

    # Conflict: different dates
    conflicts.append({
        "rule": "birth_date_conflict",
        "document_value": doc_date,
        "record_value": rec_date,
    })
    return P_BIRTH_DATE_CONFLICT


def _score_birthplace(
    doc_place: str | None,
    rec_place: str | None,
    reasons: list[dict],
    conflicts: list[dict],
) -> float:
    """Score birthplace similarity."""
    if not doc_place and not rec_place:
        reasons.append({"rule": "birthplace_missing_both", "impact": 0.0})
        return 0.0

    if not doc_place:
        reasons.append({"rule": "birthplace_missing_in_document", "impact": 0.0})
        return 0.0

    if not rec_place:
        reasons.append({"rule": "birthplace_missing_in_record", "impact": 0.0})
        return 0.0

    # Normalize for comparison
    doc_norm = doc_place.lower().strip()
    rec_norm = rec_place.lower().strip()

    if doc_norm == rec_norm:
        reasons.append({
            "rule": "birthplace_match",
            "impact": W_BIRTHPLACE_MATCH,
            "document_value": doc_place,
            "record_value": rec_place,
        })
        return W_BIRTHPLACE_MATCH

    # Partial match: one contains the other (e.g. "г. Москва" vs "Москва")
    if doc_norm in rec_norm or rec_norm in doc_norm:
        reasons.append({
            "rule": "birthplace_partial_match",
            "impact": W_BIRTHPLACE_MATCH * 0.5,
            "document_value": doc_place,
            "record_value": rec_place,
        })
        return W_BIRTHPLACE_MATCH * 0.5

    # Different places — not a conflict (birthplace is optional data)
    reasons.append({
        "rule": "birthplace_mismatch",
        "impact": 0.0,
        "document_value": doc_place,
        "record_value": rec_place,
    })
    return 0.0
