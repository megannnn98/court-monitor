"""The OpenAI-compatible wire protocol of a chat completion asked for JSON: the request
it sends and the envelope it reads. What a failure means, which answers are accepted
and what the provider's own options are belong to the caller."""

import json

import httpx
import pytest

from monitor_core.llm import (
    CHAT_ENVELOPE_ERRORS,
    ChatCompletion,
    post_json_chat,
    read_chat_completion,
)

SCHEMA: dict[str, object] = {"type": "object"}


def _post(handler: httpx.MockTransport, **options: object) -> httpx.Response:
    return post_json_chat(
        httpx.Client(transport=handler),
        "https://llm.test/chat",
        model="m",
        system="s",
        user="u",
        schema_name="x",
        schema=SCHEMA,
        headers={"Authorization": "Bearer k"},
        timeout_seconds=5.0,
        **options,  # type: ignore[arg-type]
    )


def _recording(response: httpx.Response) -> tuple[httpx.MockTransport, list[httpx.Request]]:
    sent: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return response

    return httpx.MockTransport(handle), sent


def test_the_request_body_in_wire_order() -> None:
    transport, sent = _recording(httpx.Response(200))

    _post(transport)

    [request] = sent
    assert (request.method, str(request.url)) == ("POST", "https://llm.test/chat")
    assert request.headers["authorization"] == "Bearer k"
    assert request.extensions["timeout"]["read"] == 5.0
    body = json.loads(request.content)
    assert list(body) == ["model", "messages", "response_format", "temperature"]
    assert body["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": "x", "schema": SCHEMA},
    }
    assert body["temperature"] == 0


def test_strict_and_extra_options_come_from_the_caller() -> None:
    transport, sent = _recording(httpx.Response(200))

    _post(transport, strict=True, extra_body={"max_tokens": 10, "usage": {"include": True}})

    body = json.loads(sent[0].content)
    assert list(body["response_format"]["json_schema"]) == ["name", "strict", "schema"]
    assert list(body) == [
        "model",
        "messages",
        "response_format",
        "temperature",
        "max_tokens",
        "usage",
    ]


def test_any_status_is_returned_and_network_errors_pass_through() -> None:
    transport, _ = _recording(httpx.Response(503, text="body"))
    assert _post(transport).status_code == 503

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    with pytest.raises(httpx.ConnectError):
        _post(httpx.MockTransport(refuse))


def test_the_envelope_is_read_as_sent() -> None:
    response = httpx.Response(
        200,
        json={
            "model": "served",
            "choices": [{"message": {"content": "{}"}, "finish_reason": "length"}],
            "usage": {"prompt_tokens": 3},
        },
    )

    assert read_chat_completion(response) == ChatCompletion(
        content="{}", finish_reason="length", usage={"prompt_tokens": 3}, model="served"
    )


@pytest.mark.parametrize(("sent", "read"), [(None, {}), ([1], [1]), (5, 5)])
def test_usage_is_passed_on_unchecked(sent: object, read: object) -> None:
    # Only a missing or empty usage becomes `{}`; any other value is the caller's to judge.
    response = httpx.Response(
        200, json={"choices": [{"message": {"content": "{}"}}], "usage": sent}
    )

    assert read_chat_completion(response).usage == read


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, text="not json"),
        httpx.Response(200, json={"choices": []}),
        httpx.Response(200, json={"choices": [{}]}),
        httpx.Response(200, json=[1]),
    ],
)
def test_a_broken_envelope_raises_its_own_error(response: httpx.Response) -> None:
    with pytest.raises(CHAT_ENVELOPE_ERRORS):
        read_chat_completion(response)
