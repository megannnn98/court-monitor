"""Deterministic, conservative matching of a publication to a cluster of publications.

A cluster is one event told by several publications: the same person, the same step,
the same day, in different words. The score is rule weights, not probabilities. A missing
value counts neither for nor against. The person and the step are never enough: the day
is needed, and beside it enough independent evidence — one more piece on the same day,
two a day apart or with no day at all. A contradiction forbids the join.

The contradictions (they forbid a join, whatever the score):

* the two events are about different people (a cluster is about one person);
* the two events are of different steps (a cluster is one step);
* the two events are the same step on different days (one detention is not another);
* the two events are the same step under two different articles of the Criminal Code;
* the two events name courts that have nothing in common.

And a join needs the independent evidence besides the person and the step: one more piece
on the same day, two a day apart (or with no day at all). A pair without them is a candidate,
never a join — two detentions of one person on one day that share nothing else are two
detentions.

Two clusters are never merged by this module: it decides whether one event joins one
cluster.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

VERSION = "1.1.0"
# The score a join needs. The lowest score an automatic join can have is 0.67 (the same
# person, the same step, no date, the article of the Criminal Code and the place), so
# the threshold cuts nothing the support rule allows.
AUTO_THRESHOLD = 0.60
# The shortest sentence taken as «the same words» in two articles.
MIN_QUOTE_LENGTH = 60
# The days two reports of one step may differ by (the news slips a day).
DATE_TOLERANCE_DAYS = 1

# The weights of the pieces of evidence. They add up to more than the threshold, so a
# pair with enough independent evidence joins, and a pair with little does not.
WEIGHT_TARGET = 0.30
WEIGHT_TYPE = 0.20
WEIGHT_SAME_DATE = 0.20
WEIGHT_CLOSE_DATES = 0.12
WEIGHT_COURT = 0.12
WEIGHT_PLACE = 0.10
WEIGHT_AUTHORITY = 0.08
WEIGHT_CHARGE_PART = 0.15
WEIGHT_CHARGE_ARTICLE = 0.07
WEIGHT_EXACT_QUOTE = 0.25
# The optional e5 signal: it only ranks, it never decides.
WEIGHT_TEXT_SIMILARITY = 0.05

_SCOPES = ("court", "authority", "place")
# The features that stand on their own: the person and the step are never enough.
_SUPPORTING = (
    "court",
    "authority",
    "place",
    "charge_part",
    "charge_article",
    "exact_quote",
)
# How many of them a join needs when the two events are...
# on the same day: one is enough (the same step of the same person on the same day,
#     with the same court or the same article);
# a day apart, or with no day at all: two (one step a day apart is not one step).
_REQUIRED_SUPPORT = {"same_date": 1, "close_dates": 2, "no_dates": 2}


@dataclass(frozen=True, slots=True)
class ClusterEvidence:
    """What one event brings to the matching. `targets` are `entity_groups.key`s of the
    people it is about; judges, lawyers and bystanders are never in it."""

    event_type: str
    event_date: date | None
    targets: frozenset[str]
    charges: frozenset[str]
    courts: frozenset[str]
    authorities: frozenset[str]
    places: frozenset[str]
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
            "quote_hash": self.quote_hash,
            "quote_length": self.quote_length,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> ClusterEvidence:
        day = data.get("event_date")
        return cls(
            event_type=data["event_type"],
            event_date=date.fromisoformat(day) if day else None,
            targets=frozenset(data["targets"]),
            charges=frozenset(data["charges"]),
            courts=frozenset(data["courts"]),
            authorities=frozenset(data["authorities"]),
            places=frozenset(data["places"]),
            quote_hash=data["quote_hash"],
            quote_length=data["quote_length"],
        )

    def scope(self, name: str) -> frozenset[str]:
        return {"court": self.courts, "authority": self.authorities, "place": self.places}[name]


@dataclass(frozen=True, slots=True)
class Match:
    """What the comparison found: the score, the pieces it rests on, the contradictions.

    `support` is the number of the independent features the join rests on (the person
    and the step do not count). A join needs enough of them: one on the same day, two
    a day apart or with no day at all. A pair without them is a candidate, never a
    join: two detentions of one person on the same day without anything else in
    common are two detentions.
    """

    score: float
    features: tuple[str, ...]
    vetoes: tuple[str, ...] = ()
    support: int = 0
    required_support: int = 1

    @property
    def automatic(self) -> bool:
        return (
            self.score >= AUTO_THRESHOLD
            and not self.vetoes
            and self.support >= self.required_support
        )


def _articles(charges: frozenset[str]) -> frozenset[str]:
    return frozenset(charge.split("/")[0] for charge in charges)


def _date_gap(a: ClusterEvidence, b: ClusterEvidence) -> int | None:
    """The days between the two events; None when a date is unknown."""
    if a.event_date is None or b.event_date is None:
        return None
    return abs((a.event_date - b.event_date).days)


def score_pair(
    a: ClusterEvidence, b: ClusterEvidence, *, text_similarity: float | None = None
) -> Match:
    """What the two events have in common, and what forbids them to be one event.

    `text_similarity` is the optional cosine of the e5 vectors of the two quotes. It
    only adds a little to the score: the similarity of the words is never enough on
    its own to put two events in one cluster.
    """
    features: list[str] = []
    vetoes: list[str] = []
    score = 0.0

    same_person = bool(a.targets & b.targets)
    if same_person:
        features.append("same_target")
        score += WEIGHT_TARGET
    elif a.targets and b.targets:
        # Two people: not one event. A cluster is about one person.
        vetoes.append("different_targets")

    if a.event_type == b.event_type:
        features.append("same_type")
        score += WEIGHT_TYPE
    else:
        # One step of the proceedings: its own cluster, its own case link.
        vetoes.append("different_event_type")

    gap = _date_gap(a, b)
    if gap is None:
        required = _REQUIRED_SUPPORT["no_dates"]
    elif gap == 0:
        features.append("same_date")
        score += WEIGHT_SAME_DATE
        required = _REQUIRED_SUPPORT["same_date"]
    elif gap <= DATE_TOLERANCE_DAYS:
        features.append("close_dates")
        score += WEIGHT_CLOSE_DATES
        required = _REQUIRED_SUPPORT["close_dates"]
    else:
        required = _REQUIRED_SUPPORT["close_dates"]
        if a.event_type == b.event_type:
            vetoes.append("different_dates_of_same_step")

    if a.charges & b.charges:
        features.append("charge_part")
        score += WEIGHT_CHARGE_PART
    elif _articles(a.charges) & _articles(b.charges):
        features.append("charge_article")
        score += WEIGHT_CHARGE_ARTICLE
    elif a.charges and b.charges:
        # One step under two different articles of the Criminal Code: two cases.
        vetoes.append("different_charge_articles")

    for name in _SCOPES:
        if a.scope(name) & b.scope(name):
            features.append(name)
            score += {
                "court": WEIGHT_COURT,
                "authority": WEIGHT_AUTHORITY,
                "place": WEIGHT_PLACE,
            }[name]
    if a.courts and b.courts and not (a.courts & b.courts):
        vetoes.append("incompatible_courts")

    if a.quote_hash == b.quote_hash and a.quote_length >= MIN_QUOTE_LENGTH:
        features.append("exact_quote")
        score += WEIGHT_EXACT_QUOTE

    if text_similarity is not None and text_similarity >= 0.9:
        features.append("text_similarity")
        score += WEIGHT_TEXT_SIMILARITY

    support = sum(1 for name in features if name in _SUPPORTING)
    if "exact_quote" in features:
        # The very same words are two independent features: a day apart is one step.
        support = max(support, 2)
    return Match(round(score, 4), tuple(features), tuple(vetoes), support, required)
