"""The Jev pre-filter: strict reading of the answer, the cache, and what each policy drops."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from evaluation.jev_prefilter.client import (
    DECISIONS_URL,
    MODEL,
    AnswerCache,
    Jev,
    JevAnswerError,
    cache_key,
    classify,
    parse_answer,
)
from evaluation.jev_prefilter.experiment import (
    FORBIDDEN,
    Article,
    ForbiddenDataError,
    allowed,
    ask_all,
    load_articles,
    policy_metrics,
    verdict,
)
from evaluation.jev_prefilter.policy import POLICIES, THRESHOLDS, decide
from processing.junk.drop_verifier_prompt import (
    DECISION,
    DIAGNOSTIC_OPTIONS,
    DROP,
    KEEP,
    PROMPT_VERSION,
    RUBRIC_VERSION,
    UNCERTAIN,
    request_for,
)

SNAPSHOT = f"{MODEL}-20260917"


def _choice(options: tuple[str, ...], chosen: str, p: float) -> dict[str, Any]:
    rest = round((1 - p) / (len(options) - 1), 6)
    return {
        "type": "choice",
        "choice": chosen,
        "probabilities": {o: (p if o == chosen else rest) for o in options},
        "confidence": p,
    }


def _answer(
    choice: str = DROP,
    probabilities: dict[str, float] | None = None,
    *,
    model: str = SNAPSHOT,
    subject: str = "person",
) -> dict[str, Any]:
    decision = {
        "type": "choice",
        "choice": choice,
        "probabilities": probabilities or {KEEP: 0.05, DROP: 0.9, UNCERTAIN: 0.05},
        "confidence": 0.9,
    }
    answers: dict[str, Any] = {DECISION: decision}
    for name, options in DIAGNOSTIC_OPTIONS.items():
        answers[name] = _choice(options, subject if name == "subject" else options[0], 0.8)
    return {"model": model, "answers": answers, "usage": {"cost": 0.0005}, "id": "gen-1"}


def _jev(handler: Any) -> Jev:
    return Jev(lambda: httpx.Client(transport=httpx.MockTransport(handler)), "key", sleep=0)


def _record(choice: str, p_drop: float, *, subject: str = "person") -> dict[str, Any]:
    p_other = round((1 - p_drop) / 2, 6)
    probabilities = {KEEP: p_other, DROP: p_drop, UNCERTAIN: p_other}
    readings = parse_answer(_answer(choice, probabilities, subject=subject))
    return {name: r.model_dump() for name, r in readings.items()}


# --- parsing -------------------------------------------------------------------------------


@pytest.mark.parametrize("choice", [KEEP, DROP, UNCERTAIN])
def test_each_of_the_three_choices_is_read(choice: str) -> None:
    probabilities = {KEEP: 0.2, DROP: 0.3, UNCERTAIN: 0.5}
    reading = parse_answer(_answer(choice, probabilities))[DECISION]
    assert reading.choice == choice
    assert reading.probabilities == probabilities
    assert reading.confidence == 0.9


def test_the_diagnostic_questions_are_read_too() -> None:
    readings = parse_answer(_answer(subject="organization"))
    assert set(readings) == {DECISION, *DIAGNOSTIC_OPTIONS}
    assert readings["subject"].choice == "organization"


def test_a_missing_choice_is_an_error() -> None:
    answer = _answer()
    del answer["answers"][DECISION]["choice"]
    with pytest.raises(JevAnswerError):
        parse_answer(answer)


def test_a_choice_outside_the_options_is_an_error() -> None:
    with pytest.raises(JevAnswerError):
        parse_answer(_answer("MAYBE"))


def test_a_missing_question_is_an_error() -> None:
    answer = _answer()
    del answer["answers"]["process"]
    with pytest.raises(JevAnswerError):
        parse_answer(answer)


@pytest.mark.parametrize(
    "probabilities",
    [
        {KEEP: 0.5, DROP: 0.5},  # an option without a probability
        {KEEP: 0.2, DROP: "high", UNCERTAIN: 0.1},
        {KEEP: 0.2, DROP: 1.4, UNCERTAIN: 0.1},
    ],
)
def test_probabilities_that_are_not_one_per_option_in_0_1_are_an_error(
    probabilities: dict[str, Any],
) -> None:
    with pytest.raises(JevAnswerError):
        parse_answer(_answer(DROP, probabilities))


# --- the client ----------------------------------------------------------------------------


def test_the_request_asks_the_pinned_model_with_the_article_as_the_record() -> None:
    asked: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == DECISIONS_URL
        asked.append(json.loads(request.content))
        return httpx.Response(200, json=_answer())

    jev = _jev(handler)
    jev.ask(request_for("Заголовок\n\nтекст"))
    assert asked[0]["model"] == MODEL
    assert asked[0]["state"]["records"] == [{"id": "article", "record": "Заголовок\n\nтекст"}]
    assert set(asked[0]["questions"]) == {DECISION, *DIAGNOSTIC_OPTIONS}
    assert jev.calls == 1
    assert jev.cost_usd == pytest.approx(0.0005)


@pytest.mark.parametrize("answered", ["typesafe/jev-1.12", "openai/gpt-5", None])
def test_an_answer_from_another_model_is_an_error_and_nothing_falls_back(
    answered: str | None,
) -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content)["model"])
        return httpx.Response(200, json=_answer(model=answered))  # type: ignore[arg-type]

    with pytest.raises(JevAnswerError, match="answered by"):
        _jev(handler).ask(request_for("x"))
    # One request, to the pinned model only: no retry and no other model on a foreign answer.
    assert seen == [MODEL]


def test_a_refused_request_is_an_error_without_retry() -> None:
    seen: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(1)
        return httpx.Response(401, json={"error": "no"})

    with pytest.raises(JevAnswerError):
        _jev(handler).ask(request_for("x"))
    assert seen == [1]


def test_the_cache_answers_a_repeated_article_without_asking(tmp_path: Path) -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(200, json=_answer())

    jev = _jev(handler)
    path = tmp_path / "cache.jsonl"
    first, hit_first = classify(7, "Т", "текст", jev, AnswerCache(path))
    # A new cache over the same file: the answer survives the process.
    second, hit_second = classify(7, "Т", "текст", jev, AnswerCache(path))
    assert (hit_first, hit_second) == (False, True)
    assert calls == [1]
    assert second == first
    assert first["readings"][DECISION]["choice"] == DROP
    assert first["model_id"] == SNAPSHOT
    assert first["prompt_version"] == PROMPT_VERSION


def test_the_cache_key_changes_with_every_part_it_is_made_of() -> None:
    base = cache_key(MODEL, "Т", "текст")
    assert cache_key(MODEL, "Т", "текст") == base
    assert cache_key("typesafe/jev-1.12", "Т", "текст") != base
    assert cache_key(MODEL, "T2", "текст") != base
    assert cache_key(MODEL, "Т", "текст ") != base
    assert cache_key(MODEL, "Т", "текст", prompt_version="v2") != base
    assert cache_key(MODEL, "Т", "текст", rubric_version="r2") != base
    assert (PROMPT_VERSION, RUBRIC_VERSION) == (
        "jev-strong-prefilter-v1",
        "court-monitor-person-criminal-case-v2",
    )


def test_an_unreadable_answer_is_not_cached(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        answer = _answer()
        del answer["answers"][DECISION]["choice"]
        return httpx.Response(200, json=answer)

    path = tmp_path / "cache.jsonl"
    with pytest.raises(JevAnswerError):
        classify(7, "Т", "текст", _jev(handler), AnswerCache(path))
    assert not path.exists()


# --- the policies --------------------------------------------------------------------------


def test_the_policies_are_argmax_and_the_fixed_thresholds_only() -> None:
    assert THRESHOLDS == (0.5, 0.6, 0.7, 0.8, 0.9)
    assert POLICIES == ("argmax", "p_drop>=0.50", "p_drop>=0.60", "p_drop>=0.70",
                        "p_drop>=0.80", "p_drop>=0.90")  # fmt: skip


def test_argmax_drops_exactly_a_drop_choice() -> None:
    assert decide(_record(DROP, 0.4), "argmax") == "DROP"
    assert decide(_record(KEEP, 0.1), "argmax") == "PASS"
    assert decide(_record(UNCERTAIN, 0.3), "argmax") == "PASS"


def test_a_threshold_drops_at_and_above_it() -> None:
    assert decide(_record(DROP, 0.9), "p_drop>=0.90") == "DROP"
    assert decide(_record(DROP, 0.89), "p_drop>=0.90") == "PASS"
    assert decide(_record(DROP, 0.89), "p_drop>=0.80") == "DROP"
    assert decide(_record(DROP, 0.5), "p_drop>=0.50") == "DROP"
    assert decide(_record(DROP, 0.49), "p_drop>=0.50") == "PASS"


@pytest.mark.parametrize("choice", [KEEP, UNCERTAIN])
@pytest.mark.parametrize("policy", POLICIES)
def test_keep_and_uncertain_always_pass(choice: str, policy: str) -> None:
    # Even when the probabilities disagree with the choice: only a DROP choice drops.
    assert decide(_record(choice, 0.95), policy) == "PASS"


@pytest.mark.parametrize("policy", POLICIES)
def test_an_article_without_an_answer_passes(policy: str) -> None:
    assert decide(None, policy) == "PASS"


@pytest.mark.parametrize("policy", POLICIES)
def test_the_diagnostic_answers_do_not_drive_the_decision(policy: str) -> None:
    outcomes = {
        decide(_record(choice, p, subject=subject), policy)
        for subject in DIAGNOSTIC_OPTIONS["subject"]
        for choice, p in [(DROP, 0.95)]
    }
    assert outcomes == {"DROP"}
    outcomes = {
        decide(_record(KEEP, 0.0, subject=subject), policy)
        for subject in DIAGNOSTIC_OPTIONS["subject"]
    }
    assert outcomes == {"PASS"}


def test_an_unknown_policy_is_an_error() -> None:
    with pytest.raises(ValueError):
        decide(_record(DROP, 0.95), "p_drop>=0.55")


# --- the experiment ------------------------------------------------------------------------


def _article(article_id: int, label: int) -> Article:
    return Article(article_id, f"Т{article_id}", f"текст {article_id}", label)


def test_an_api_error_leaves_the_article_unanswered_and_it_passes(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        record = json.loads(request.content)["state"]["records"][0]["record"]
        if "текст 2" in record:
            return httpx.Response(503, text="down")
        return httpx.Response(200, json=_answer(DROP))

    rows = [_article(1, 0), _article(2, 1)]
    records, hits, errors = ask_all(rows, _jev(handler), AnswerCache(tmp_path / "c.jsonl"))
    assert set(records) == {1}
    assert set(errors) == {2}
    assert hits == 0
    metrics = policy_metrics(rows, records, "argmax")
    # The relevant article Jev could not judge is not dropped.
    assert (metrics["recall"], metrics["FN"], metrics["drop_count"]) == (1.0, 0, 1)


def test_policy_metrics_count_dropped_relevant_articles_as_false_negatives() -> None:
    rows = [_article(1, 1), _article(2, 1), _article(3, 0), _article(4, 0)]
    records = {
        1: {"readings": _record(KEEP, 0.1)},
        2: {"readings": _record(DROP, 0.85)},
        3: {"readings": _record(DROP, 0.95)},
        4: {"readings": _record(UNCERTAIN, 0.2)},
    }
    assert policy_metrics(rows, records, "p_drop>=0.80") == {
        "policy": "p_drop>=0.80",
        "recall": 0.5,
        "FN": 1,
        "FP": 1,
        "drop_count": 2,
        "pass_count": 2,
        "drop_pct": 50.0,
        "pass_pct": 50.0,
    }
    assert policy_metrics(rows, records, "p_drop>=0.90")["FN"] == 0


def _m(policy: str, recall: float, drop_pct: float) -> dict[str, Any]:
    return {"policy": policy, "recall": recall, "drop_count": int(drop_pct * 10),
            "drop_pct": drop_pct}  # fmt: skip


def test_the_verdict_takes_the_most_aggressive_policy_with_enough_recall() -> None:
    metrics = [_m("argmax", 0.96, 70), _m("p_drop>=0.50", 0.975, 55), _m("p_drop>=0.90", 1.0, 30)]
    outcome, chosen = verdict(metrics)
    assert chosen is not None and chosen["policy"] == "p_drop>=0.50"
    assert outcome == "SUCCESS"
    assert verdict([_m("argmax", 0.99, 50)])[0] == "EXCELLENT"
    assert verdict([_m("argmax", 0.985, 50)])[0] == "GOOD"
    assert verdict([_m("argmax", 0.98, 39)])[0] == "FAIL-REDUCTION"
    assert verdict([_m("argmax", 0.969, 60)]) == ("FAIL-RECALL", None)


@pytest.mark.parametrize("path", FORBIDDEN)
def test_the_holdouts_and_the_validation_are_never_read(path: Path) -> None:
    with pytest.raises(ForbiddenDataError):
        load_articles(path, [1])
    with pytest.raises(ForbiddenDataError):
        allowed(path / "final_labels.jsonl")


def test_an_answer_that_is_not_an_object_is_an_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[_answer()])

    with pytest.raises(JevAnswerError, match="not an object"):
        _jev(handler).ask(request_for("x"))
