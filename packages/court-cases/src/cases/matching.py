"""Deterministic, conservative matching of an event to a case.

Scores are rule weights, not probabilities. A missing value counts neither for nor against
a match. A name plus a Criminal Code article is never enough: an automatic tie needs more
independent evidence (the exact article part, a court, a body, a place, the same words or a
case number), and a contradiction (two case numbers, two dates of one step, a sentence
before the arrest) forbids it. No publication-time window: cases last for years.

Two cases are never merged by this module; it only decides whether one event joins one case.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

VERSION = "1.0.0"
AUTO_THRESHOLD = 0.85

# The shortest sentence taken as «the same words» in two articles.
MIN_QUOTE_LENGTH = 60
# A verdict cannot come before the steps that lead to it.
_VERDICTS = frozenset({"sentence", "fine"})
_PROCEEDINGS = frozenset({"case_opened", "search", "detention", "arrest", "charge"})
_SCOPES = ("authority", "court", "place")


@dataclass(frozen=True, slots=True)
class Evidence:
    """What one event brings to the matching. `targets` are `entity_groups.key`s of the
    people it is about; judges, lawyers and bystanders are never in it."""

    event_type: str
    event_date: date | None
    targets: frozenset[str]
    charges: frozenset[str]
    courts: frozenset[str]
    authorities: frozenset[str]
    places: frozenset[str]
    case_number: str | None
    quote_hash: str
    quote_length: int

    def to_json(self) -> dict[str, Any]:
        return {
            "event_type": self.event_type,
            "event_date": self.event_date.isoformat() if self.event_date else None,
            "targets": sorted(self.targets),
            "charges": sorted(self.charges),
            "courts": sorted(self.courts),
            "authorities": sorted(self.authorities),
            "places": sorted(self.places),
            "case_number": self.case_number,
            "quote_hash": self.quote_hash,
            "quote_length": self.quote_length,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Evidence:
        day = data["event_date"]
        return cls(
            event_type=data["event_type"],
            event_date=date.fromisoformat(day) if day else None,
            targets=frozenset(data["targets"]),
            charges=frozenset(data["charges"]),
            courts=frozenset(data["courts"]),
            authorities=frozenset(data["authorities"]),
            places=frozenset(data["places"]),
            case_number=data["case_number"],
            quote_hash=data["quote_hash"],
            quote_length=data["quote_length"],
        )

    def scope(self, name: str) -> frozenset[str]:
        return {"court": self.courts, "authority": self.authorities, "place": self.places}[name]


@dataclass(frozen=True, slots=True)
class Match:
    score: float
    features: tuple[str, ...]
    vetoes: tuple[str, ...] = ()

    @property
    def automatic(self) -> bool:
        return self.score >= AUTO_THRESHOLD and not self.vetoes


def _articles(charges: frozenset[str]) -> frozenset[str]:
    return frozenset(charge.split("/")[0] for charge in charges)


def _stage_conflict(a: Evidence, b: Evidence) -> bool:
    """A verdict dated before a step of the proceedings, in either order."""
    for verdict, step in ((a, b), (b, a)):
        verdict_day, step_day = verdict.event_date, step.event_date
        if (
            verdict_day is not None
            and step_day is not None
            and verdict.event_type in _VERDICTS
            and step.event_type in _PROCEEDINGS
            and verdict_day < step_day
        ):
            return True
    return False


def score_pair(a: Evidence, b: Evidence) -> Match:
    features: list[str] = []
    vetoes: list[str] = []
    same_person = bool(a.targets & b.targets)
    if same_person:
        features.append("same_target")
    if a.charges & b.charges:
        features.append("charge_part")
    elif _articles(a.charges) & _articles(b.charges):
        features.append("charge_article")
    for name in _SCOPES:
        if a.scope(name) & b.scope(name):
            features.append(name)
    same_quote = a.quote_hash == b.quote_hash and a.quote_length >= MIN_QUOTE_LENGTH
    if same_quote:
        features.append("exact_quote")

    # Numbering is often local to a court or a body: a bare number never suffices.
    if a.case_number and b.case_number:
        if a.case_number != b.case_number:
            vetoes.append("different_case_numbers")
        else:
            features.append("case_number")
            conflict = any(
                a.scope(name) and b.scope(name) and not (a.scope(name) & b.scope(name))
                for name in _SCOPES
            )
            if conflict:
                vetoes.append("case_number_scope_conflict")
            elif any(a.scope(name) & b.scope(name) for name in _SCOPES):
                return Match(1.0, tuple(features), tuple(vetoes))
    same_type = a.event_type == b.event_type
    if same_type and a.event_date and b.event_date and a.event_date != b.event_date:
        vetoes.append("different_dates_of_same_event_type")
    if _stage_conflict(a, b):
        vetoes.append("stage_order_conflict")

    support = [name for name in ("charge_part", "court", "authority", "place") if name in features]
    if same_person and same_type:
        # Two reports of one step: the same words, or the same article part and a place.
        # An undated pair is no pair of one day: it needs two pieces of evidence.
        if same_quote or (a.event_date is not None and a.event_date == b.event_date and support):
            return Match(0.90, tuple(features), tuple(vetoes))
        if len(support) >= 2:
            return Match(0.88, tuple(features), tuple(vetoes))
    if same_person and not same_type and "charge_part" in support and len(support) >= 2:
        # Another step of the proceedings: the exact article part and a shared place or body.
        return Match(0.88, tuple(features), tuple(vetoes))
    return Match(0.40 if same_person else 0.0, tuple(features), tuple(vetoes))


def score_case(evidence: Evidence, members: list[Evidence]) -> Match:
    matches = [score_pair(evidence, member) for member in members]
    if not matches:
        return Match(0.0, ())
    best = max(matches, key=lambda match: (match.score, len(match.features)))
    # No other member lets a contradiction of the case number through.
    numbers = {
        veto
        for match in matches
        for veto in match.vetoes
        if veto in {"different_case_numbers", "case_number_scope_conflict"}
    }
    return Match(best.score, best.features, tuple(sorted(set(best.vetoes) | numbers)))


def choose_case(candidates: list[tuple[str, Match]]) -> str | None:
    """The case an event joins: the only one that matches automatically. Two strong
    matches are an ambiguity, never settled by a margin."""
    accepted = [key for key, match in candidates if match.automatic]
    return accepted[0] if len(accepted) == 1 else None
