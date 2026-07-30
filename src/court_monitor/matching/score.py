"""Deterministic, explainable scoring for person match candidates.

All weights are module-level constants — easy to tune without touching logic.
"""

from __future__ import annotations

import re
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
W_BIRTH_YEAR_MATCH = 0.15
W_BIRTHPLACE_MATCH = 0.10

P_BIRTH_DATE_CONFLICT = -0.50
P_BIRTH_YEAR_CONFLICT = -0.60
P_NAME_CONFLICT = -0.40

# Threshold for creating a candidate
CANDIDATE_THRESHOLD = 0.45

ALGORITHM_VERSION = "match-v3"


@dataclass(frozen=True)
class BirthDateEvidence:
    """Explicit representation of birth date data precision.

    Never fabricate month/day from year-only data.
    """

    year: str | None = None
    month: str | None = None
    day: str | None = None
    precision: str = "none"  # "full", "year", "none"

    @classmethod
    def from_full_date(cls, date_str: str | None) -> BirthDateEvidence:
        """Parse YYYY-MM-DD or DD.MM.YYYY into full evidence."""
        if not date_str:
            return cls()
        m = re.match(r"(\d{4})-(\d{2})-(\d{2})", date_str)
        if m:
            return cls(year=m.group(1), month=m.group(2), day=m.group(3), precision="full")
        m = re.match(r"(\d{2})\.(\d{2})\.(\d{4})", date_str)
        if m:
            return cls(year=m.group(3), month=m.group(2), day=m.group(1), precision="full")
        m = re.match(r"(\d{4})", date_str)
        if m:
            return cls(year=m.group(1), precision="year")
        return cls()

    @classmethod
    def from_year(cls, year: str) -> BirthDateEvidence:
        """Create year-only evidence (no fabrication of month/day)."""
        return cls(year=year, precision="year")

    @property
    def iso_date(self) -> str | None:
        """Return full ISO date if available, None otherwise."""
        if self.year and self.month and self.day:
            return f"{self.year}-{self.month}-{self.day}"
        return None

    @property
    def display(self) -> str:
        """Human-readable representation."""
        if self.precision == "full":
            return self.iso_date or ""
        if self.precision == "year":
            return self.year or ""
        return "-"


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
    doc_birth_date: BirthDateEvidence | None,
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
    birth_date_score = _score_birth_date(doc_birth_date, record_birth_date, reasons, conflicts)
    birthplace_score = _score_birthplace(doc_birth_place, record_birth_place, reasons, conflicts)

    total = max(0.0, min(1.0, name_score + birth_date_score + birthplace_score))

    return ScoreResult(
        score=total,
        name_score=name_score,
        birth_date_score=birth_date_score,
        birthplace_score=birthplace_score,
        reasons=reasons,
        conflicts=conflicts,
    )


def _is_initial_token(token: str) -> bool:
    """True for a single-letter-plus-dot token (e.g. "и."), not a full given name."""
    return len(token) <= 2 and token.endswith(".")


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

    # Use nominative forms for comparison
    doc_surname = doc.surname
    rec_surname = rec.surname
    doc_name = doc.name
    rec_name = rec.name
    doc_patronymic = doc.patronymic
    rec_patronymic = rec.patronymic

    surname_match = doc_surname == rec_surname
    name_match = doc_name == rec_name if (doc_name and rec_name) else False
    patronymic_match = (
        doc_patronymic == rec_patronymic if (doc_patronymic and rec_patronymic) else False
    )

    # Check for conflict: different surname is a strong negative signal
    if not surname_match:
        conflicts.append(
            {
                "rule": "surname_mismatch",
                "document_value": doc.raw,
                "record_value": rec.raw,
            }
        )
        return 0.0

    return _score_matching_surname(
        doc,
        rec,
        doc_name=doc_name,
        rec_name=rec_name,
        doc_patronymic=doc_patronymic,
        rec_patronymic=rec_patronymic,
        name_match=name_match,
        patronymic_match=patronymic_match,
        reasons=reasons,
        conflicts=conflicts,
    )


def _score_matching_surname(
    doc: NormalizedName,
    rec: NormalizedName,
    *,
    doc_name: str,
    rec_name: str,
    doc_patronymic: str,
    rec_patronymic: str,
    name_match: bool,
    patronymic_match: bool,
    reasons: list[dict],
    conflicts: list[dict],
) -> float:
    """Score given-name/patronymic similarity once the surname is known to match.

    A dispatcher over the mutually exclusive cases, strongest evidence first.
    Each branch is its own function so the rule it encodes can be read — and
    tested — without holding the whole cascade in mind.
    """
    if name_match and patronymic_match:
        return _score_full_name(doc, rec, reasons)

    if name_match:
        return _score_surname_and_name(
            doc,
            rec,
            doc_name=doc_name,
            rec_name=rec_name,
            doc_patronymic=doc_patronymic,
            rec_patronymic=rec_patronymic,
            patronymic_match=patronymic_match,
            reasons=reasons,
            conflicts=conflicts,
        )

    if _both_give_a_full_given_name(doc_name, rec_name):
        return _score_given_name_conflict(doc_name, rec_name, reasons, conflicts)

    initials = _score_shared_initials(doc, rec, reasons)
    if initials is not None:
        return initials

    return _score_surname_only(doc, rec, reasons)


def _score_full_name(doc: NormalizedName, rec: NormalizedName, reasons: list[dict]) -> float:
    reasons.append(
        {
            "rule": "full_name_morphological_match",
            "impact": W_FULL_NAME_MATCH,
            "document_value": doc.raw,
            "record_value": rec.raw,
            "normalized_doc": doc.nominative,
            "normalized_rec": rec.nominative,
        }
    )
    return W_FULL_NAME_MATCH


def _score_surname_and_name(
    doc: NormalizedName,
    rec: NormalizedName,
    *,
    doc_name: str,
    rec_name: str,
    doc_patronymic: str,
    rec_patronymic: str,
    patronymic_match: bool,
    reasons: list[dict],
    conflicts: list[dict],
) -> float:
    """Surname and given name agree; a patronymic known on both sides and
    differing still counts against the match."""
    impact = W_SURNAME_NAME_MATCH
    if doc_patronymic and rec_patronymic and not patronymic_match:
        conflicts.append(
            {
                "rule": "patronymic_mismatch",
                "document_value": doc_patronymic,
                "record_value": rec_patronymic,
            }
        )
        impact += P_NAME_CONFLICT

    reasons.append(
        {
            "rule": "surname_name_match",
            "impact": max(0.0, impact),
            "document_value": f"{doc.surname} {doc_name}",
            "record_value": f"{rec.surname} {rec_name}",
        }
    )
    return max(0.0, impact)


def _both_give_a_full_given_name(doc_name: str, rec_name: str) -> bool:
    """True when both sides name a person outright rather than by initial.

    Without this, two different people sharing a surname and initial letters —
    but with known, differing first names — would fall through to the initials
    branch and score as a match with no conflict shown to the reviewer.
    """
    doc_full = bool(doc_name) and not _is_initial_token(doc_name)
    rec_full = bool(rec_name) and not _is_initial_token(rec_name)
    return doc_full and rec_full


def _score_given_name_conflict(
    doc_name: str, rec_name: str, reasons: list[dict], conflicts: list[dict]
) -> float:
    conflicts.append(
        {"rule": "given_name_mismatch", "document_value": doc_name, "record_value": rec_name}
    )
    reasons.append({"rule": "given_name_mismatch", "impact": 0.0})
    return 0.0


def _score_shared_initials(
    doc: NormalizedName, rec: NormalizedName, reasons: list[dict]
) -> float | None:
    """Returns None when the initials do not line up, so the caller falls through."""
    if not (doc.initials and rec.initials):
        return None
    doc_init = doc.initials[1:] if len(doc.initials) > 1 else ""
    rec_init = rec.initials[1:] if len(rec.initials) > 1 else ""
    if not (doc_init and rec_init and doc_init == rec_init):
        return None

    reasons.append(
        {
            "rule": "surname_initials_match",
            "impact": W_SURNAME_INITIALS_MATCH,
            "document_value": f"{doc.surname} {doc.initials}",
            "record_value": f"{rec.surname} {rec.initials}",
        }
    )
    return W_SURNAME_INITIALS_MATCH


def _score_surname_only(doc: NormalizedName, rec: NormalizedName, reasons: list[dict]) -> float:
    reasons.append(
        {
            "rule": "surname_only_match",
            "impact": W_SURNAME_INITIALS_MATCH * 0.5,
            "document_value": doc.surname,
            "record_value": rec.surname,
        }
    )
    return W_SURNAME_INITIALS_MATCH * 0.5


def _extract_year(date_str: str | None) -> str | None:
    """Extract year from a date string (YYYY-MM-DD or YYYY)."""
    if not date_str:
        return None
    m = re.match(r"(\d{4})", date_str)
    return m.group(1) if m else None


def _score_birth_date(
    doc_evidence: BirthDateEvidence | None,
    rec_date: str | None,
    reasons: list[dict],
    conflicts: list[dict],
) -> float:
    """Score birth date similarity.

    Uses BirthDateEvidence to distinguish year-only from full date.
    Never fabricates month/day from year-only data.
    """
    rec_year = _extract_year(rec_date)
    rec_evidence = BirthDateEvidence.from_full_date(rec_date)

    if not doc_evidence or doc_evidence.precision == "none":
        if not rec_date:
            reasons.append({"rule": "birth_date_missing_both", "impact": 0.0})
        else:
            reasons.append({"rule": "birth_date_missing_in_document", "impact": 0.0})
        return 0.0

    if not rec_date:
        reasons.append({"rule": "birth_date_missing_in_record", "impact": 0.0})
        return 0.0

    # Full date match (both have full precision and match)
    if (
        doc_evidence.precision == "full"
        and rec_evidence.precision == "full"
        and doc_evidence.iso_date == rec_evidence.iso_date
    ):
        reasons.append(
            {
                "rule": "birth_date_match",
                "impact": W_BIRTH_DATE_MATCH,
                "document_value": doc_evidence.display,
                "record_value": rec_evidence.display,
            }
        )
        return W_BIRTH_DATE_MATCH

    # Year match (at least year matches)
    if doc_evidence.year and rec_year and doc_evidence.year == rec_year:
        reasons.append(
            {
                "rule": "birth_year_match",
                "impact": W_BIRTH_YEAR_MATCH,
                "document_value": doc_evidence.year,
                "record_value": rec_year,
                "precision": doc_evidence.precision,
            }
        )
        return W_BIRTH_YEAR_MATCH

    # Year conflict
    if doc_evidence.year and rec_year and doc_evidence.year != rec_year:
        conflicts.append(
            {
                "rule": "birth_year_conflict",
                "document_value": doc_evidence.year,
                "record_value": rec_year,
            }
        )
        return P_BIRTH_YEAR_CONFLICT

    # Date conflict (same year, different full date)
    conflicts.append(
        {
            "rule": "birth_date_conflict",
            "document_value": doc_evidence.display,
            "record_value": rec_evidence.display,
        }
    )
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

    doc_norm = doc_place.lower().strip()
    rec_norm = rec_place.lower().strip()

    if doc_norm == rec_norm:
        reasons.append(
            {
                "rule": "birthplace_match",
                "impact": W_BIRTHPLACE_MATCH,
                "document_value": doc_place,
                "record_value": rec_place,
            }
        )
        return W_BIRTHPLACE_MATCH

    if doc_norm in rec_norm or rec_norm in doc_norm:
        reasons.append(
            {
                "rule": "birthplace_partial_match",
                "impact": W_BIRTHPLACE_MATCH * 0.5,
                "document_value": doc_place,
                "record_value": rec_place,
            }
        )
        return W_BIRTHPLACE_MATCH * 0.5

    reasons.append(
        {
            "rule": "birthplace_mismatch",
            "impact": 0.0,
            "document_value": doc_place,
            "record_value": rec_place,
        }
    )
    return 0.0
