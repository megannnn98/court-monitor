"""Together AI client against a mocked HTTP transport (no network)."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from llm.together_client import TogetherConfig, TogetherStructuredLlmClient
from research.workflow.llm import (
    LlmAuthenticationError,
    LlmConfigurationError,
    LlmError,
    LlmInvalidResponseError,
    LlmRateLimitError,
    LlmRequestRejectedError,
    LlmTimeoutError,
    LlmUnavailableError,
)

CONFIG = TogetherConfig(api_key="secret-key-123", model="some/model", timeout_seconds=5)
SCHEMA = {"type": "object", "properties": {"request": {"type": ["object", "null"]}}}


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> TogetherStructuredLlmClient:
    return TogetherStructuredLlmClient(
        CONFIG, http_client=httpx.Client(transport=httpx.MockTransport(handler))
    )


def _completion(content: Any, *, finish_reason: str = "stop", **extra: Any) -> dict[str, Any]:
    return {
        "model": "some/model",
        "choices": [
            {"message": {"role": "assistant", "content": content}, "finish_reason": finish_reason}
        ],
        **extra,
    }


def _call(client: TogetherStructuredLlmClient) -> Any:
    return client.complete_json(
        system_prompt="SYSTEM",
        user_message="QUERY",
        schema_name="research_intake",
        json_schema=SCHEMA,
    )


def test_sends_json_schema_request_and_parses_structured_output() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json=_completion(
                '{"request": {"object_type": "person"}}',
                usage={"prompt_tokens": 120, "completion_tokens": 15, "total_tokens": 135},
            ),
        )

    result = _call(_client(handler))

    (request,) = seen
    assert str(request.url) == "https://api.together.ai/v1/chat/completions"
    assert request.headers["Authorization"] == "Bearer secret-key-123"
    assert json.loads(request.content) == {
        "model": "some/model",
        "messages": [
            {"role": "system", "content": "SYSTEM"},
            {"role": "user", "content": "QUERY"},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "research_intake", "schema": SCHEMA},
        },
        "temperature": 0,
    }
    assert result.data == {"request": {"object_type": "person"}}
    assert (result.usage.prompt_tokens, result.usage.completion_tokens) == (120, 15)


@pytest.mark.parametrize(
    ("status", "error_type"),
    [
        (401, LlmAuthenticationError),
        (403, LlmAuthenticationError),
        (429, LlmRateLimitError),
        (500, LlmUnavailableError),
        (503, LlmUnavailableError),
        (400, LlmRequestRejectedError),
        (422, LlmRequestRejectedError),
    ],
)
def test_http_errors_map_to_typed_errors_without_body(
    status: int, error_type: type[LlmError]
) -> None:
    client = _client(lambda request: httpx.Response(status, json={"error": "echo QUERY secret"}))

    with pytest.raises(error_type) as exc_info:
        _call(client)

    assert str(exc_info.value) == f"Together AI returned HTTP {status}"


def test_timeout_maps_to_timeout_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(LlmTimeoutError):
        _call(_client(handler))


def test_connection_failure_maps_to_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(LlmUnavailableError):
        _call(_client(handler))


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, text="<html>not json</html>"),
        httpx.Response(200, json={"choices": []}),
        httpx.Response(200, json=_completion(None)),
        httpx.Response(200, json=_completion("Иванов не в перечне")),
        httpx.Response(200, json=_completion("[1, 2]")),
        httpx.Response(200, json=_completion('{"request": {', finish_reason="length")),
    ],
)
def test_malformed_output_is_invalid_response(response: httpx.Response) -> None:
    with pytest.raises(LlmInvalidResponseError):
        _call(_client(lambda request: response))


def test_config_from_env_requires_key_and_model_and_hides_key() -> None:
    with pytest.raises(LlmConfigurationError, match="TOGETHER_API_KEY, TOGETHER_MODEL"):
        TogetherConfig.from_env({})

    config = TogetherConfig.from_env(
        {"TOGETHER_API_KEY": "k-secret", "TOGETHER_MODEL": "m", "TOGETHER_TIMEOUT_SECONDS": "12.5"}
    )

    assert (config.model, config.timeout_seconds) == ("m", 12.5)
    assert "k-secret" not in repr(config)


@pytest.mark.parametrize("timeout", ["abc", "0", "-1"])
def test_config_rejects_bad_timeout(timeout: str) -> None:
    with pytest.raises(LlmConfigurationError):
        TogetherConfig.from_env(
            {"TOGETHER_API_KEY": "k", "TOGETHER_MODEL": "m", "TOGETHER_TIMEOUT_SECONDS": timeout}
        )


def test_malformed_usage_block_does_not_fail_intake() -> None:
    client = _client(
        lambda request: httpx.Response(
            200, json=_completion('{"request": null}', usage={"prompt_tokens": "many"})
        )
    )

    result = _call(client)

    assert result.data == {"request": None}
    assert result.usage.prompt_tokens is None
