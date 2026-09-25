"""Asking a model within a budget, and what it cost."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from entities.llm import (
    BudgetExceededError,
    Endpoint,
    ModelError,
    Spend,
    ask_in_batches,
    chat_json,
    endpoint_from_env,
)


def _client(answer: dict[str, Any], sent: list[dict[str, Any]]) -> httpx.Client:
    def handle(request: httpx.Request) -> httpx.Response:
        sent.append(
            {"body": json.loads(request.content), "auth": request.headers.get("authorization")}
        )
        return httpx.Response(200, json=answer)

    return httpx.Client(transport=httpx.MockTransport(handle))


ANSWER = {
    "choices": [{"message": {"content": '{"ok": true}'}, "finish_reason": "stop"}],
    "usage": {"prompt_tokens": 100, "completion_tokens": 20, "cost": 0.0012},
}


def _ask(endpoint: Endpoint, http: httpx.Client, spend: Spend) -> str:
    return chat_json(
        http, endpoint, system="s", user="u", schema_name="x", schema={}, max_tokens=10, spend=spend
    )


def test_openrouter_is_asked_for_the_price_and_the_price_is_counted() -> None:
    sent: list[dict[str, Any]] = []
    spend = Spend(budget_usd=1.0)
    endpoint = Endpoint("openrouter", "https://openrouter.test", "deepseek/x", "key")

    content = _ask(endpoint, _client(ANSWER, sent), spend)
    _ask(endpoint, _client(ANSWER, sent), spend)

    assert content == '{"ok": true}'
    assert sent[0]["body"]["usage"] == {"include": True}
    assert sent[0]["auth"] == "Bearer key"
    assert (spend.calls, round(spend.cost_usd, 6)) == (2, 0.0024)


def test_the_endpoint_follows_the_environment() -> None:
    openrouter = endpoint_from_env({"OPENROUTER_API_KEY": "k", "ENTITY_NORMALIZE_MODEL": "m"})

    assert openrouter == Endpoint(
        "openrouter", "https://openrouter.ai/api/v1/chat/completions", "m", "k"
    )
    assert endpoint_from_env({}) is None


def test_a_cut_answer_is_an_error() -> None:
    cut = {"choices": [{"message": {"content": "{"}, "finish_reason": "length"}], "usage": {}}

    with pytest.raises(ModelError, match="finish_reason=length"):
        _ask(Endpoint("openrouter", "https://x.test", "m", "k"), _client(cut, []), Spend())


def test_past_the_budget_no_batch_is_sent() -> None:
    spend = Spend(budget_usd=0.25)
    sent: list[int] = []

    def ask(batch: int) -> int:
        sent.append(batch)
        spend.add(0.1)
        return batch * 10

    results = list(ask_in_batches(range(10), ask, concurrency=1, spend=spend))

    # Three calls reach the budget; the other seven are left, each said so.
    assert sent == [0, 1, 2]
    assert [result for _, result in results[:3]] == [0, 10, 20]
    assert len(results) == 10
    assert all(isinstance(result, BudgetExceededError) for _, result in results[3:])


def test_a_failed_batch_is_handed_back_not_raised() -> None:
    def ask(batch: int) -> int:
        if batch == 1:
            raise ModelError("provider down")
        return batch

    results = dict(ask_in_batches([0, 1, 2], ask, concurrency=2))

    assert results[0] == 0 and results[2] == 2 and isinstance(results[1], ModelError)
