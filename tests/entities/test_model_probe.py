"""«Тест модели»: one call through the names step's own extractor. A pass needs JSON by
its schema and every item answered; HTTP 200 alone is a failure. No test asks OpenRouter."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
from support.openrouter_catalog import CATALOG_PAYLOAD

from llm.model_choice import CatalogCache
from processing.identity import model_probe
from processing.identity.model_probe import ITEMS, probe

FREE = "nvidia/nemotron-3.5-super-vl:free"
GOOD = {
    "names": [
        {
            "id": 1,
            "source": "Александра Моора",
            "nominative": "Александр Моор",
            "gender": "male",
            "is_person": True,
        },
        {
            "id": 2,
            "source": "Ольга Щекина",
            "nominative": "Ольга Щекина",
            "gender": "female",
            "is_person": True,
        },
        {
            "id": 3,
            "source": "Свободу политзаключённым",
            "nominative": "",
            "gender": "unknown",
            "is_person": False,
        },
    ]
}


def _http(
    answer: Callable[[httpx.Request], httpx.Response], seen: list[Any] | None = None
) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(json.loads(request.content))
        return answer(request)

    return httpx.Client(transport=httpx.MockTransport(handler))


def _completion(content: object, *, model: str = FREE, cost: float | None = 0.0) -> httpx.Response:
    usage: dict[str, object] = {"prompt_tokens": 812, "completion_tokens": 96}
    if cost is not None:
        usage["cost"] = cost
    text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
    return httpx.Response(
        200,
        json={
            "model": model,
            "choices": [{"message": {"content": text}, "finish_reason": "stop"}],
            "usage": usage,
        },
    )


def _run(
    answer: Callable[[httpx.Request], httpx.Response],
    model: str = FREE,
    seen: list[Any] | None = None,
) -> model_probe.ProbeResult:
    ticks = iter([10.0, 11.25])
    return probe(
        model,
        "key",
        http_client=_http(answer, seen),
        catalog=CatalogCache(request=lambda: CATALOG_PAYLOAD).get(),
        clock=lambda: next(ticks),
    )


def test_a_good_answer_passes_with_the_entities_tokens_cost_and_time() -> None:
    seen: list[Any] = []

    result = _run(lambda _: _completion(GOOD), seen=seen)

    assert result.ok and result.error is None
    assert result.seconds == 1.25
    assert (result.prompt_tokens, result.completion_tokens, result.cost_usd) == (812, 96, 0.0)
    assert [entity.nominative for entity in result.entities] == [
        "Александр Моор",
        "Ольга Щекина",
        "",
    ]
    assert result.expected_matched == 3
    # The very request of the names step: its schema, strict, the model chosen.
    [body] = seen
    assert body["model"] == FREE
    assert body["response_format"]["json_schema"]["name"] == "names"
    assert body["response_format"]["json_schema"]["strict"] is True
    assert [item["id"] for item in json.loads(body["messages"][1]["content"])] == [
        item.id for item in ITEMS
    ]


def test_http_200_with_broken_json_is_a_failure() -> None:
    result = _run(lambda _: _completion("{names: [oops"))

    assert not result.ok
    assert result.error is not None and "не по схеме" in result.error
    assert result.entities == []


def test_json_off_the_schema_is_a_failure() -> None:
    result = _run(lambda _: _completion({"people": ["Моор"]}))

    assert not result.ok and result.error is not None and "не по схеме" in result.error


def test_an_answer_that_skips_an_item_is_a_failure() -> None:
    partial = {"names": GOOD["names"][:2]}

    result = _run(lambda _: _completion(partial))

    assert not result.ok
    assert result.error is not None and "2 из 3" in result.error
    assert len(result.entities) == 2


def test_a_rate_limit_is_said_so_and_no_other_model_is_tried() -> None:
    seen: list[Any] = []

    result = _run(
        lambda _: httpx.Response(429, json={"error": {"message": "rate limited"}}), seen=seen
    )

    assert not result.ok and result.error is not None and "429" in result.error
    assert [body["model"] for body in seen] == [FREE], "one call, of the chosen model only"


def test_a_timeout_is_said_so() -> None:
    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    result = _run(slow)

    assert not result.ok and result.error is not None and "timeout" in result.error


def test_a_free_model_that_bills_fails_the_test() -> None:
    result = _run(lambda _: _completion(GOOD, cost=0.01))

    assert not result.ok and result.error is not None and "billed" in result.error


def test_a_cost_not_reported_is_estimated_by_the_price_list_and_marked_so() -> None:
    result = _run(
        lambda _: _completion(GOOD, model="qwen/qwen3.8-flash-20260826", cost=None),
        model="qwen/qwen3.8-flash",
    )

    assert result.ok and result.cost_usd is None
    # 812 × $0.15 + 96 × $0.47 per million.
    assert result.estimated_cost_usd == round((812 * 0.15 + 96 * 0.47) / 1_000_000, 6)


def _roles(answers: list[dict[str, object]]) -> Callable[[httpx.Request], httpx.Response]:
    return lambda _: _completion({"answers": answers})


def _run_decision(answer: Callable[[httpx.Request], httpx.Response]) -> model_probe.ProbeResult:
    ticks = iter([0.0, 2.0])
    return model_probe.probe_decision(
        FREE,
        "key",
        http_client=_http(answer),
        catalog=CatalogCache(request=lambda: CATALOG_PAYLOAD).get(),
        clock=lambda: next(ticks),
    )


RIGHT = {
    1: ("Эрик Ларссон", "accused"),
    2: ("Андрей Колесов", "other"),
    3: ("Павел Рудин", "foreign"),
    4: ("Ларс Хольм", "accused"),
}


def _decided(**kinds: str) -> Callable[[httpx.Request], httpx.Response]:
    """The four answers, right unless `kinds` says otherwise (`item3="detained"`)."""
    answers = []
    for item_id, (name, kind) in RIGHT.items():
        if f"skip{item_id}" in kinds:
            continue
        answers.append(
            {
                "id": item_id,
                "source": name,
                "kind": kinds.get(f"item{item_id}", kind),
                "explanation": "—",
            }
        )
    return _roles(answers)


def test_the_decision_test_passes_when_only_the_accused_are_figurants() -> None:
    result = _run_decision(_decided())

    assert result.ok and result.expected_matched == 4
    assert [entity.role for entity in result.entities] == ["accused", "other", "foreign", "accused"]


def test_the_decision_test_fails_when_the_commentator_is_made_the_accused() -> None:
    result = _run_decision(_decided(item2="accused"))

    assert not result.ok
    assert result.error is not None and "«Андрей Колесов»: ждали" in result.error


def test_the_decision_test_fails_on_the_man_detained_abroad_made_a_figurant() -> None:
    """The mistake of 16.10.2026: a man detained abroad, beside another's Russian case,
    answered «accused» — a figurant, and so a political prisoner one step later."""
    result = _run_decision(_decided(item3="accused"))

    assert not result.ok and result.error is not None
    assert "«Павел Рудин»" in result.error and "ответила accused" in result.error


def test_the_decision_test_cuts_the_dangerous_class_not_the_taxonomy() -> None:
    """What the step makes of an answer decides: «detained» is a «possible» (a person's
    review), «support»/«other» a «mentioned» — no figurant, no politics; they pass."""
    for kind in ("other", "detained", "support"):
        assert _run_decision(_decided(item2=kind, item3=kind)).ok, kind


def test_the_decision_test_fails_when_the_accused_is_lost() -> None:
    result = _run_decision(_decided(item4="detained"))

    assert not result.ok and result.error is not None and "«Ларс Хольм»" in result.error


def test_a_missing_answer_and_a_wrong_one_are_both_told_in_the_decision_test() -> None:
    result = _run_decision(_decided(skip1="", item3="accused"))

    assert not result.ok and result.expected_matched == 2
    assert result.error is not None
    assert "3 из 4" in result.error and "«Павел Рудин»" in result.error


def test_valid_json_with_a_wrong_name_fails_the_test() -> None:
    """The schema and every item answered are not enough: the names must be right."""
    wrong = {
        "names": [
            {**GOOD["names"][0], "nominative": "Александра Моора"},
            GOOD["names"][1],
            {**GOOD["names"][2], "is_person": True, "nominative": "Свобода Политзаключённым"},
        ]
    }

    result = _run(lambda _: _completion(wrong))

    assert not result.ok and result.expected_matched == 1
    assert result.error is not None and "«Александра Моора»" in result.error


def test_a_missing_item_and_a_wrong_name_are_both_told() -> None:
    both = {"names": [{**GOOD["names"][0], "nominative": "Александра Моора"}, GOOD["names"][1]]}

    result = _run(lambda _: _completion(both))

    assert not result.ok and result.error is not None
    assert "2 из 3" in result.error and "«Александра Моора»" in result.error


def test_the_decision_test_asks_three_times_and_one_wrong_round_fails_it() -> None:
    """A model may answer the same question differently (the free model gave foreign,
    detained, foreign on the very same quote): every round must be right."""
    rounds = iter([_decided(), _decided(item3="accused"), _decided()])
    seen: list[object] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return next(rounds)(request)

    result = _run_decision(answer)

    assert not result.ok and len(seen) == 2, "the failing round ends the test"
    assert result.error is not None and "В прогоне 2 из 3" in result.error
    assert len(_calls_of(_decided())) == model_probe.DECISION_ROUNDS


def _calls_of(answer: Callable[[httpx.Request], httpx.Response]) -> list[object]:
    seen: list[object] = []

    def counting(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return answer(request)

    assert _run_decision(counting).ok
    return seen
