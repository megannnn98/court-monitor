"""The entity model chosen in the console: the catalog's prices, the free models, what a
call may and may not do (`llm.model_choice`, `llm.chat`). No test asks OpenRouter."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from support.openrouter_catalog import CATALOG_PAYLOAD

from llm import model_choice
from llm.chat import (
    LEDGER,
    Endpoint,
    ModelError,
    ModelHttpError,
    ModelTimeoutError,
    Spend,
    chat_json,
    is_free_model,
    served_by_asked,
)
from llm.model_choice import (
    ENTITY_MODELS,
    Catalog,
    CatalogCache,
    model_info,
    parse_catalog,
    price_text,
)

URL = "https://openrouter.test/chat"


def _catalog() -> Catalog:
    return CatalogCache(request=lambda: CATALOG_PAYLOAD).get()


def test_the_console_offers_ten_models_with_deepseek_mimo_qwen_and_space_bunny() -> None:
    ids = [option.id for option in ENTITY_MODELS]

    assert len(ids) == len(set(ids)) == 10
    assert ids[0] == "deepseek/deepseek-v4.1-flash" == model_choice.DEFAULT_MODEL
    for family in ("deepseek/", "xiaomi/mimo", "qwen/", "stealth/space-bunny"):
        assert any(model.startswith(family) for model in ids), family
    assert any(is_free_model(model) for model in ids)


def test_prices_are_read_per_million_tokens_from_the_catalog() -> None:
    entries = parse_catalog(CATALOG_PAYLOAD)

    flash = entries["deepseek/deepseek-v4.1-flash"]
    assert flash.price is not None
    assert (flash.price.prompt, flash.price.completion) == (0.09, 0.18)
    assert flash.structured_outputs
    assert not entries["google/gemma-4-31b-it:free"].structured_outputs
    # -1 is a router's «depends on the route»: no price, not a negative one.
    assert entries["openrouter/auto"].price is None


def test_free_models_are_told_by_the_suffix_and_by_a_zero_price() -> None:
    catalog = _catalog()

    free = model_info("nvidia/nemotron-3.5-super-vl:free", catalog)
    paid = model_info("nvidia/nemotron-3.5-super-vl", catalog)

    assert free.free and free.usable
    assert not paid.free
    assert price_text(free, catalog).startswith("FREE")
    assert "Платная" in price_text(paid, catalog)
    # Known free by its id even with no catalog at all.
    assert model_info("nvidia/nemotron-3.5-super-vl:free", Catalog({}, None)).free


def test_a_model_the_catalog_does_not_list_is_unusable() -> None:
    catalog = _catalog()

    bunny = model_info("stealth/space-bunny-alpha", catalog)
    gemma = model_info("google/gemma-4-31b-it:free", catalog)

    assert bunny.listed is False and not bunny.usable
    assert gemma.structured_outputs is False and not gemma.usable


def test_the_catalog_is_cached_and_a_failure_keeps_old_prices_marked_stale() -> None:
    clock = [0.0]
    answers: list[object] = [CATALOG_PAYLOAD]
    asked: list[float] = []

    def request() -> object:
        asked.append(clock[0])
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    cache = CatalogCache(request=request, now=lambda: clock[0])
    first = cache.get()
    clock[0] = 3599.0
    assert cache.get() is first and len(asked) == 1

    clock[0] = 3601.0
    answers.append(httpx.ConnectError("down"))
    stale = cache.get()
    assert stale.stale and stale.error and "ConnectError" in stale.error
    info = model_info("deepseek/deepseek-v4.1-flash", stale)
    assert info.price is not None and info.price_stale
    assert "Цена устарела" in price_text(info, stale)

    # A failed catalog is asked again a minute later, not on every page.
    clock[0] = 3630.0
    assert cache.get() is stale and len(asked) == 2
    clock[0] = 3662.0
    answers.append(CATALOG_PAYLOAD)
    assert not cache.get().stale and len(asked) == 3


def test_an_unknown_price_is_said_so_never_shown_as_zero() -> None:
    nothing = CatalogCache(request=lambda: (_ for _ in ()).throw(httpx.ConnectError("x"))).get()

    info = model_info("deepseek/deepseek-v4.1-flash", nothing)

    assert info.price is None and not info.free and info.listed is None
    assert price_text(info, nothing) == "Цена сейчас неизвестна: каталог OpenRouter не ответил."


def test_the_answer_must_come_from_the_model_asked() -> None:
    assert served_by_asked("deepseek/deepseek-v4.1-flash", "deepseek/deepseek-v4.1-flash-20260910")
    assert served_by_asked(
        "nvidia/nemotron-3.5-super-vl:free", "nvidia/nemotron-3.5-super-vl-20261015:free"
    )
    assert served_by_asked("qwen/qwen3.8-flash", None)
    assert not served_by_asked("nvidia/nemotron-3.5-super-vl:free", "openai/gpt-oss-120b")
    # A suffix other than a date is another model, though it starts alike.
    for other in ("-thinking", "-other", "-experimental", "-2026"):
        assert not served_by_asked(
            "deepseek/deepseek-v4.1-flash", "deepseek/deepseek-v4.1-flash" + other
        )


# -- one call ---------------------------------------------------------------------------


def _client(handler: Any) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _completion(
    *, model: str, cost: float | None = 0.0001, content: str = '{"names": []}'
) -> dict[str, object]:
    usage: dict[str, object] = {"prompt_tokens": 120, "completion_tokens": 30}
    if cost is not None:
        usage["cost"] = cost
    return {
        "model": model,
        "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
        "usage": usage,
    }


def _ask(endpoint: Endpoint, http: httpx.Client, spend: Spend | None = None) -> str:
    return chat_json(
        http,
        endpoint,
        system="s",
        user="u",
        schema_name="names",
        schema={"type": "object"},
        max_tokens=100,
        spend=spend,
    )


def test_the_request_names_only_the_chosen_model_and_no_fallback() -> None:
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=_completion(model="qwen/qwen3.8-flash-20260826"))

    endpoint = Endpoint("openrouter", URL, "qwen/qwen3.8-flash", "key")
    _ask(endpoint, _client(handler))

    [body] = seen
    assert body["model"] == "qwen/qwen3.8-flash"
    # No `models` list: OpenRouter never moves to another model, paid or not.
    assert "models" not in body and "route" not in body
    assert body["provider"] == {"require_parameters": True}


def test_a_free_model_that_bills_fails_and_stops_the_run() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json=_completion(model="nvidia/nemotron-3.5-super-vl:free", cost=0.02)
        )

    spend = Spend(2.0)
    endpoint = Endpoint("openrouter", URL, "nvidia/nemotron-3.5-super-vl:free", "key")

    with pytest.raises(ModelError, match="was billed"):
        _ask(endpoint, _client(handler), spend)
    assert spend.exhausted(), "no further batch may be sent"


def test_an_answer_of_another_model_is_a_failure() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_completion(model="openai/gpt-4o"))

    endpoint = Endpoint("openrouter", URL, "nvidia/nemotron-3.5-super-vl:free", "key")

    with pytest.raises(ModelError, match="another model"):
        _ask(endpoint, _client(handler))


def test_a_rate_limit_and_a_timeout_are_told_apart() -> None:
    def limited(_: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": {"message": "Rate limit exceeded"}})

    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    endpoint = Endpoint("openrouter", URL, "nvidia/nemotron-3.5-super-vl:free", "key")

    with pytest.raises(ModelHttpError) as limit:
        _ask(endpoint, _client(limited))
    assert limit.value.status_code == 429
    with pytest.raises(ModelTimeoutError):
        _ask(endpoint, _client(slow))


def test_the_ledger_counts_tokens_and_cost_per_model_only_while_on() -> None:
    answers = iter([0.0004, None, 0.0004])

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json=_completion(model="xiaomi/mimo-v2.6-flash", cost=next(answers))
        )

    endpoint = Endpoint("openrouter", URL, "xiaomi/mimo-v2.6-flash", "key")
    http = _client(handler)
    LEDGER.reset()
    spend = Spend()
    try:
        LEDGER.active = True
        _ask(endpoint, http, spend)
        _ask(endpoint, http, spend)
        LEDGER.active = False
        _ask(endpoint, http, spend)
        tallies = LEDGER.tallies()
    finally:
        LEDGER.active = False
        LEDGER.reset()

    tally = tallies["xiaomi/mimo-v2.6-flash"]
    assert (tally.calls, tally.prompt_tokens, tally.completion_tokens) == (2, 240, 60)
    assert tally.cost_usd == pytest.approx(0.0004)
    # The call without a reported cost is counted apart, for an estimate.
    assert (tally.uncosted_calls, tally.uncosted_prompt_tokens) == (1, 120)
    assert spend.calls == 3 and spend.uncosted_calls == 1


def test_the_dated_and_free_shapes_openrouter_answers_are_the_model_asked() -> None:
    assert served_by_asked("x/y", "x/y:free")
    assert served_by_asked("x/y:free", "x/y-20260101:free")
    assert served_by_asked("x/y", "x/y-20260101")
    # A snapshot of the snapshot is no answer of the model asked: one date only.
    assert not served_by_asked("x/y", "x/y-20260101-20260102")
