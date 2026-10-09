"""One OpenAI-compatible chat completion asked for JSON: the request it sends and what it
hands back. Budgets, logging and the schema of the answer belong to the caller."""

import json

import httpx
import pytest

from monitor_core.llm import ChatCompletion, Endpoint, ModelError, request_json_chat

SCHEMA: dict[str, object] = {"type": "object"}


def _ask(endpoint: Endpoint, response: httpx.Response) -> tuple[ChatCompletion, httpx.Request]:
    sent: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return response

    completion = request_json_chat(
        httpx.Client(transport=httpx.MockTransport(handle)),
        endpoint,
        system="s",
        user="u",
        schema_name="x",
        schema=SCHEMA,
        max_tokens=10,
        timeout_seconds=5.0,
    )
    return completion, sent[0]


def _choice(content: object, finish_reason: object = "stop") -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [{"message": {"content": content}, "finish_reason": finish_reason}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2, "cost": "0.5"},
        },
    )


def test_the_request_names_the_schema_and_the_endpoint_options() -> None:
    completion, request = _ask(
        Endpoint("openrouter", "https://or.test/chat", "m", "k"), _choice('{"a": 1}')
    )

    assert str(request.url) == "https://or.test/chat"
    assert request.headers["authorization"] == "Bearer k"
    assert request.extensions["timeout"]["read"] == 5.0
    assert json.loads(request.content)["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": "x", "strict": True, "schema": SCHEMA},
    }
    assert completion.text() == '{"a": 1}'
    assert completion.usage == {"prompt_tokens": 3, "completion_tokens": 2, "cost": "0.5"}
    assert completion.cost_usd == 0.5


def test_a_cut_or_empty_answer_is_returned_and_refused_only_when_read() -> None:
    cut, _ = _ask(Endpoint("local", "http://l.test", "m"), _choice("{", "length"))
    empty, _ = _ask(Endpoint("local", "http://l.test", "m"), _choice(None))

    assert cut.finish_reason == "length"
    with pytest.raises(ModelError, match="finish_reason=length"):
        cut.text()
    with pytest.raises(ModelError, match="no content"):
        empty.text()


def test_an_http_error_raises_before_anything_is_returned() -> None:
    with pytest.raises(ModelError, match="HTTP 502: bad gateway"):
        _ask(Endpoint("local", "http://l.test", "m"), httpx.Response(502, text="bad gateway"))
