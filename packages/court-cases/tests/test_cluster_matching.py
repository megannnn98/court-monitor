"""The rules of the event clusters: what may be joined and what may not.

The weights are rule weights, not probabilities, and the join needs the independent
evidence besides the person and the step: one more piece on the same day, two a day
apart. A pair without them is a candidate, never a join.
"""

from __future__ import annotations

from datetime import date

from cases.clusters.matching import (
    AUTO_THRESHOLD,
    VERSION,
    ClusterEvidence,
    score_pair,
)


def _evidence(**changes: object) -> ClusterEvidence:
    base: dict[str, object] = {
        "event_type": "detention",
        "event_date": date(2024, 3, 1),
        "targets": frozenset({"иван петров"}),
        "charges": frozenset(),
        "courts": frozenset(),
        "authorities": frozenset(),
        "places": frozenset(),
        "quote_hash": "a" * 64,
        "quote_length": 80,
    }
    base.update(changes)
    return ClusterEvidence(**base)  # type: ignore[arg-type]


def test_the_version_is_raised_with_the_rule() -> None:
    assert VERSION == "1.1.0"


def test_the_person_and_the_step_alone_are_never_enough() -> None:
    """The same person, the same step, the same day, nothing else: not a join."""
    match = score_pair(_evidence(), _evidence(quote_hash="b" * 64))
    assert match.score >= AUTO_THRESHOLD
    assert not match.automatic
    assert match.support == 0
    assert match.required_support == 1


def test_a_day_apart_needs_two_independent_features() -> None:
    """A day apart, one feature: not a join. Two features: a join."""
    first = _evidence(places=frozenset({"Москва"}))
    second = _evidence(
        event_date=date(2024, 3, 2),
        quote_hash="b" * 64,
        places=frozenset({"Москва"}),
    )
    one = score_pair(first, second)
    assert not one.automatic
    assert one.support == 1
    assert one.required_support == 2

    third = _evidence(
        event_date=date(2024, 3, 2),
        quote_hash="c" * 64,
        places=frozenset({"Москва"}),
        charges=frozenset({"205.2"}),
    )
    fourth = _evidence(
        event_date=date(2024, 3, 2),
        quote_hash="d" * 64,
        places=frozenset({"Москва"}),
        charges=frozenset({"205.2"}),
    )
    two = score_pair(third, fourth)
    assert two.automatic
    assert two.support == 2


def test_the_same_words_are_two_features() -> None:
    """The very same words are enough a day apart."""
    first = _evidence()
    second = _evidence(event_date=date(2024, 3, 2))
    match = score_pair(first, second)
    assert match.automatic
    assert match.support == 2


def test_the_threshold_does_not_cut_a_legitimate_join() -> None:
    """The lowest score an automatic join can have is above the threshold."""
    first = _evidence(charges=frozenset({"205.2"}), places=frozenset({"Москва"}))
    second = _evidence(
        event_date=None,
        quote_hash="b" * 64,
        charges=frozenset({"205.2"}),
        places=frozenset({"Москва"}),
    )
    match = score_pair(first, second)
    assert match.automatic
    assert match.score >= AUTO_THRESHOLD
    assert match.score == 0.75


def test_the_contradictions_stay() -> None:
    """The contradictions forbid a join whatever the score."""
    first = _evidence(
        courts=frozenset({"Мосгорсуд"}), places=frozenset({"Москва"}), charges=frozenset({"205.2"})
    )
    second = _evidence(
        quote_hash="b" * 64,
        courts=frozenset({"Омский облсуд"}),
        places=frozenset({"Омск"}),
        charges=frozenset({"228.1"}),
    )
    match = score_pair(first, second)
    assert not match.automatic
    assert "incompatible_courts" in match.vetoes
    assert "different_charge_articles" in match.vetoes
