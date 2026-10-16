"""The measurement of the two-Jev junk screen: the gate it counts is the production one."""

from __future__ import annotations

import json
from collections.abc import Sequence

import httpx
import pytest

from evaluation.jev_cascade.experiment import (
    Case,
    cascade_counts,
    choose,
    gate_metrics,
    keeps,
)
from processing.junk.decision_screen import CUTOFF
from processing.junk.drop_verifier import ERROR, MIN_P_DROP, CascadeScreen, DropVerifier, Verdict


def _case(article_id: int, relevant: bool, jev1: float, jev2: Verdict | None) -> Case:
    return Case(article_id, f"t{article_id}", relevant, jev1, jev2)


SURE_DROP = Verdict(decision="DROP", p_drop=0.95)
UNSURE_DROP = Verdict(decision="DROP", p_drop=0.55)
KEEP = Verdict(decision="KEEP", p_drop=0.05)
FAILED = Verdict(decision=ERROR, error="timeout")


def test_the_cascade_drops_only_what_both_drop() -> None:
    assert keeps(_case(1, True, 0.9, None), 0.5, 0.5)
    assert not keeps(_case(2, False, 0.1, SURE_DROP), 0.5, 0.5)
    assert keeps(_case(3, True, 0.1, KEEP), 0.5, 0.5)
    assert keeps(_case(4, True, 0.1, FAILED), 0.5, 0.5)
    assert keeps(_case(5, True, 0.1, UNSURE_DROP), 0.5, 0.6)
    # Alone: JEV-1 drops under its cutoff, JEV-2 on a sure DROP.
    assert not keeps(_case(6, True, 0.1, KEEP), 0.5, None)
    assert not keeps(_case(7, True, 0.9, SURE_DROP), None, 0.5)
    with pytest.raises(ValueError, match="not asked"):
        keeps(_case(8, True, 0.1, None), 0.5, 0.5)


class _Scores:
    name = "jev1"
    cutoff = CUTOFF

    def __init__(self, scores: Sequence[float]) -> None:
        self._scores = list(scores)

    def scores(self, articles: Sequence[tuple[str, str]]) -> list[float]:
        return self._scores


def test_the_measured_gate_is_the_production_cascade() -> None:
    """At the production cutoff and threshold, `keeps` says what `CascadeScreen` does."""
    below = Verdict(decision="DROP", p_drop=MIN_P_DROP - 0.01)
    verdicts = {"a": SURE_DROP, "b": KEEP, "c": below, "d": None, "e": FAILED}
    scores = [0.1, 0.2, 0.3, 0.9, 0.4]
    raw = {
        "a": {"choice": "DROP", "probabilities": {"DROP": 0.95, "KEEP": 0.03, "UNCERTAIN": 0.02}},
        "b": {"choice": "KEEP", "probabilities": {"DROP": 0.05, "KEEP": 0.9, "UNCERTAIN": 0.05}},
        "c": {
            "choice": "DROP",
            "probabilities": {"DROP": MIN_P_DROP - 0.01, "KEEP": 0.3, "UNCERTAIN": 0.21},
        },
    }

    def handler(request: httpx.Request) -> httpx.Response:
        title = json.loads(request.content)["state"]["records"][0]["record"].split("\n")[0]
        if title == "e":
            return httpx.Response(500)
        return httpx.Response(
            200, json={"model": "typesafe/jev-1.13", "answers": {"decision": raw[title]}}
        )

    verifier = DropVerifier(
        lambda: httpx.Client(transport=httpx.MockTransport(handler)), "k", sleep=0
    )
    production = CascadeScreen(_Scores(scores), verifier).scores(
        [(title, "") for title in verdicts]
    )
    measured = [
        keeps(_case(i, True, s, v), CUTOFF, MIN_P_DROP)
        for i, (s, v) in enumerate(zip(scores, verdicts.values(), strict=True))
    ]
    assert [s >= CUTOFF for s in production] == measured == [False, True, True, True, True]


def test_gate_metrics_count_kept_as_positive() -> None:
    cases = [
        _case(1, True, 0.9, None),  # kept, relevant: TP
        _case(2, False, 0.9, None),  # kept, junk: FP
        _case(3, True, 0.1, SURE_DROP),  # dropped, relevant: FN
        _case(4, False, 0.1, SURE_DROP),  # dropped, junk: TN
    ]
    m = gate_metrics(cases, 0.5, 0.5)
    assert (m["tp"], m["fp"], m["fn"], m["tn"]) == (1, 1, 1, 1)
    assert (m["precision"], m["recall"], m["f1"]) == (0.5, 0.5, 0.5)
    assert m["lost_ids"] == [3] and m["dropped_share"] == 0.5


def test_the_rescue_is_counted_on_the_relevant_articles_jev1_drops() -> None:
    cases = [
        _case(1, True, 0.1, KEEP),  # JEV-1 false drop, rescued
        _case(2, True, 0.1, SURE_DROP),  # JEV-1 false drop, still lost
        _case(3, False, 0.1, FAILED),  # junk kept by an error
        _case(4, False, 0.1, SURE_DROP),
        _case(5, True, 0.8, None),
    ]
    counts = cascade_counts(cases, 0.5, 0.5)
    assert counts["jev1_false_drops"] == 2 and counts["jev1_false_drop_ids"] == [1, 2]
    assert counts["rescued_by_jev2"] == 1 and counts["still_lost_ids"] == [2]
    assert (counts["jev1_drop"], counts["jev2_called"], counts["jev2_error"]) == (4, 4, 1)
    assert (counts["jev2_keep_rescue"], counts["jev2_drop_confirmed"]) == (2, 2)
    assert (counts["final_keep"], counts["final_drop"], counts["jev2_rescued_junk"]) == (3, 2, 1)


def test_the_choice_puts_recall_first() -> None:
    # One relevant article JEV-1 drops at 0.5, rescued only by a strict JEV-2.
    cases = [
        _case(1, True, 0.4, UNSURE_DROP),
        *(_case(10 + i, False, 0.01, SURE_DROP) for i in range(5)),
        _case(20, False, 0.4, SURE_DROP),
    ]
    choice = choose(cases)
    # Not 0.5/0.5, which drops more but loses the relevant one.
    assert choice["cascade"] == {"cutoff": 0.5, "p_drop": 0.6}
    assert choice["jev1_alone"] == {"cutoff": 0.3}
