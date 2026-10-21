"""The optional e5 signal of the event clusters: it ranks, it never decides.

The signal is off by default. When it is on, it only adds a little to the score: two
events whose words look alike still need the person, the step and the day, and the
contradictions still forbid the join.
"""

from __future__ import annotations

from datetime import date

from cases.clusters.embeddings import TextSimilarity
from cases.clusters.matching import ClusterEvidence, score_pair


def _evidence(**changes: object) -> ClusterEvidence:
    base = {
        "event_type": "detention",
        "event_date": date(2024, 3, 1),
        "targets": frozenset({"иван петров"}),
        "charges": frozenset({"205.2/3"}),
        "courts": frozenset({"Мосгорсуд"}),
        "authorities": frozenset({"ФСБ"}),
        "places": frozenset({"Москва"}),
        "quote_hash": "a" * 64,
        "quote_length": 80,
    }
    base.update(changes)
    return ClusterEvidence(**base)  # type: ignore[arg-type]


def test_the_signal_is_off_by_default() -> None:
    similarity = TextSimilarity()
    assert similarity.enabled is False
    assert similarity(None, None) is None  # type: ignore[arg-type]
    assert similarity.vector("hash", "слова") is None  # type: ignore[arg-type]


def test_the_similarity_never_decides_alone() -> None:
    """Two events with nothing in common stay apart even with the same words."""
    first = _evidence()
    second = _evidence(
        event_type="arrest",
        targets=frozenset({"пётр сидоров"}),
        charges=frozenset({"228/1"}),
        courts=frozenset({"Омский облсуд"}),
        quote_hash="b" * 64,
    )
    without = score_pair(first, second)
    with_signal = score_pair(first, second, text_similarity=0.99)

    assert not without.automatic
    assert not with_signal.automatic
    assert "different_targets" in with_signal.vetoes
    assert with_signal.score > without.score


def test_the_similarity_is_only_a_little_of_the_score() -> None:
    """A pair that matches on its own does not need the signal."""
    first = _evidence()
    second = _evidence(quote_hash="b" * 64)
    without = score_pair(first, second)
    with_signal = score_pair(first, second, text_similarity=0.99)

    assert without.automatic
    assert with_signal.automatic
    assert with_signal.score - without.score < 0.1
