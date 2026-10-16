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
