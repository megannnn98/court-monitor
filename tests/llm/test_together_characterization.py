"""Characterization of the Together AI client and the ER review on top of it.

Pins the wire request (URL, auth, body and its key order, timeout), the answer it
returns (data, model, usage), each failure's type, message and cause, that the
provider's body never reaches a message, which `finish_reason` values are refused,
which httpx errors are not mapped at all, and the transient flag the ER review puts
on each. No network: an httpx MockTransport answers.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from llm.entity_reviewer import LlmEntityMatchReviewer, review_user_message
from llm.structured import (
    LlmAuthenticationError,
    LlmError,
    LlmInvalidResponseError,
    LlmRateLimitError,
    LlmRequestRejectedError,
    LlmTimeoutError,
    LlmUnavailableError,
    LlmUsage,
)
from llm.together_client import TogetherConfig, TogetherStructuredLlmClient
from persons.resolution.ai_review import (
    ENTITY_REVIEW_RESPONSE_SCHEMA,
    ENTITY_REVIEW_SYSTEM_PROMPT,
    CandidateReviewContext,
    EntityReviewError,
    EntityReviewRequest,
    EvidenceExcerpt,
    MentionReviewContext,
)

SECRET = "echo of QUERY with secret-key-123"
CONFIG = TogetherConfig(
    api_key="secret-key-123",
    model="config/model",
    timeout_seconds=5,
    base_url="http://127.0.0.1:11434/v1",
)
SCHEMA = {"type": "object"}


def _client(
    handler: Callable[[httpx.Request], httpx.Response],
) -> tuple[TogetherStructuredLlmClient, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    client = TogetherStructuredLlmClient(
        CONFIG, http_client=httpx.Client(transport=httpx.MockTransport(record))
    )
    return client, seen


def _envelope(content: Any, finish_reason: Any = "stop", **extra: Any) -> dict[str, Any]:
    return {
        "choices": [{"message": {"content": content}, "finish_reason": finish_reason}],
        **extra,
    }


def _call(client: TogetherStructuredLlmClient) -> Any:
    return client.complete_json(
        system_prompt="SYSTEM", user_message="QUERY", schema_name="name", json_schema=SCHEMA
    )


def _raised(handler: Callable[[httpx.Request], httpx.Response]) -> LlmError:
    client, seen = _client(handler)
    with pytest.raises(LlmError) as caught:
        _call(client)
    assert len(seen) == 1
    return caught.value


def test_the_wire_request() -> None:
    client, seen = _client(lambda _: httpx.Response(200, json=_envelope("{}")))

    _call(client)

    [request] = seen
    assert (request.method, str(request.url)) == (
        "POST",
        "http://127.0.0.1:11434/v1/chat/completions",
    )
    assert request.headers["authorization"] == "Bearer secret-key-123"
    assert request.extensions["timeout"] == {"connect": 5, "read": 5, "write": 5, "pool": 5}
    body = json.loads(request.content)
    assert body == {
        "model": "config/model",
        "messages": [
            {"role": "system", "content": "SYSTEM"},
            {"role": "user", "content": "QUERY"},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "name", "schema": SCHEMA},
        },
        "temperature": 0,
    }
    # The bytes on the wire keep this order too.
    assert list(body) == ["model", "messages", "response_format", "temperature"]
    assert list(body["response_format"]["json_schema"]) == ["name", "schema"]


def test_the_answer_its_model_and_usage() -> None:
    usage = {"prompt_tokens": 120, "completion_tokens": 15, "total_tokens": 135}
    client, _ = _client(
        lambda _: httpx.Response(
            200, json=_envelope('{"a": [1]}', model="served/model", usage=usage)
        )
    )

    result = _call(client)

    assert result.data == {"a": [1]}
    assert result.model == "served/model"
    assert result.usage == LlmUsage(prompt_tokens=120, completion_tokens=15, total_tokens=135)


@pytest.mark.parametrize(
    ("extra", "model", "usage"),
    [
        ({}, "config/model", LlmUsage()),
        ({"model": 7, "usage": None}, "config/model", LlmUsage()),
        ({"usage": {"prompt_tokens": "many"}}, "config/model", LlmUsage()),
        ({"usage": {"total_tokens": 9, "cost": 0.1}}, "config/model", LlmUsage(total_tokens=9)),
    ],
)
def test_a_missing_model_or_usage_falls_back(
    extra: dict[str, Any], model: str, usage: LlmUsage
) -> None:
    client, _ = _client(lambda _: httpx.Response(200, json=_envelope("{}", **extra)))

    result = _call(client)

    assert (result.model, result.usage) == (model, usage)


@pytest.mark.parametrize("finish_reason", ["stop", None, "content_filter", "tool_calls", "eos"])
def test_only_a_length_cut_is_refused(finish_reason: Any) -> None:
    client, _ = _client(lambda _: httpx.Response(200, json=_envelope("{}", finish_reason)))

    assert _call(client).data == {}


def test_a_length_cut_is_refused() -> None:
    error = _raised(lambda _: httpx.Response(200, json=_envelope('{"a":', "length")))

    assert type(error) is LlmInvalidResponseError
    assert str(error) == "Together AI output was truncated (finish_reason=length)"
    assert error.__cause__ is None


@pytest.mark.parametrize(
    ("response", "cause"),
    [
        (httpx.Response(200, text=SECRET), json.JSONDecodeError),
        (httpx.Response(200, json={"choices": []}), IndexError),
        (httpx.Response(200, json={"detail": SECRET}), KeyError),
        (httpx.Response(200, json={"choices": [{"text": SECRET}]}), KeyError),
        (httpx.Response(200, json={"choices": None}), TypeError),
        (httpx.Response(200, json=[SECRET]), TypeError),
    ],
)
def test_an_unexpected_envelope(response: httpx.Response, cause: type[Exception]) -> None:
    error = _raised(lambda _: response)

    assert type(error) is LlmInvalidResponseError
    assert str(error) == "Together AI response has unexpected shape"
    assert type(error.__cause__) is cause


@pytest.mark.parametrize(
    ("content", "message", "cause"),
    [
        (None, "Together AI returned no text content", None),
        (["parts"], "Together AI returned no text content", None),
        (SECRET, "Together AI output is not valid JSON", json.JSONDecodeError),
        ("[1, 2]", "Together AI output is not a JSON object", None),
    ],
)
def test_an_unusable_answer(content: Any, message: str, cause: type[Exception] | None) -> None:
    error = _raised(lambda _: httpx.Response(200, json=_envelope(content)))

    assert type(error) is LlmInvalidResponseError
    assert str(error) == message
    assert (type(error.__cause__) if error.__cause__ else None) is cause


@pytest.mark.parametrize(
    ("status", "error_type"),
    [
        (400, LlmRequestRejectedError),
        (401, LlmAuthenticationError),
        (403, LlmAuthenticationError),
        (404, LlmRequestRejectedError),
        (422, LlmRequestRejectedError),
        (429, LlmRateLimitError),
        (500, LlmUnavailableError),
        (502, LlmUnavailableError),
        (503, LlmUnavailableError),
    ],
)
def test_an_http_error_is_typed_and_never_shows_the_body(
    status: int, error_type: type[LlmError]
) -> None:
    error = _raised(lambda _: httpx.Response(status, json={"error": SECRET}))

    assert type(error) is error_type
    assert str(error) == f"Together AI returned HTTP {status}"
    assert error.__cause__ is None
    assert "secret" not in repr(error) and "QUERY" not in repr(error)


@pytest.mark.parametrize(
    ("exception", "error_type", "message"),
    [
        (httpx.ReadTimeout(SECRET), LlmTimeoutError, "Together AI request timed out after 5s"),
        (httpx.ConnectTimeout(SECRET), LlmTimeoutError, "Together AI request timed out after 5s"),
        (httpx.PoolTimeout(SECRET), LlmTimeoutError, "Together AI request timed out after 5s"),
        (
            httpx.ConnectError(SECRET),
            LlmUnavailableError,
            "Together AI is unreachable: ConnectError",
        ),
        (httpx.ReadError(SECRET), LlmUnavailableError, "Together AI is unreachable: ReadError"),
        (
            httpx.RemoteProtocolError(SECRET),
            LlmUnavailableError,
            "Together AI is unreachable: RemoteProtocolError",
        ),
    ],
)
def test_a_network_failure_is_typed_and_chained(
    exception: Exception, error_type: type[LlmError], message: str
) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        raise exception

    error = _raised(handler)

    assert type(error) is error_type
    assert str(error) == message
    assert error.__cause__ is exception


@pytest.mark.parametrize(
    "exception", [httpx.TooManyRedirects("loop"), httpx.DecodingError("bad gzip")]
)
def test_other_httpx_errors_are_not_mapped(exception: Exception) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        raise exception

    client, _ = _client(handler)
    with pytest.raises(type(exception)):
        _call(client)


# The ER review on top of the client: which failures it retries (`transient`).


def _request() -> EntityReviewRequest:
    return EntityReviewRequest(
        decision_id=7,
        mention=MentionReviewContext(
            mention_id=3,
            name="Иван Иванов",
            evidence=(EvidenceExcerpt(article_id=1, text="Суд арестовал Ивана Иванова"),),
        ),
        candidate=CandidateReviewContext(
            person_id=42,
            canonical_name="Иван Иванов",
            matching_key="иванов иван",
            resolution_score=0.8,
            surname_match="exact",
            given_name_match="exact",
            patronymic_match="missing",
        ),
        deterministic_score=0.8,
        matched_features=("surname:exact", "given_name:exact"),
    )


def _review(handler: Callable[[httpx.Request], httpx.Response]) -> EntityReviewError:
    client, seen = _client(handler)
    reviewer = LlmEntityMatchReviewer(client, model="config/model", provider="together")
    with pytest.raises(EntityReviewError) as caught:
        reviewer.review(_request())
    assert len(seen) == 1
    return caught.value


def test_the_review_request_goes_out_as_before() -> None:
    answer = {
        "decision": "same_person",
        "confidence": 0.9,
        "supporting_evidence": [],
        "conflicting_evidence": [],
        "explanation": "x",
    }
    client, seen = _client(lambda _: httpx.Response(200, json=_envelope(json.dumps(answer))))

    result = LlmEntityMatchReviewer(client, model="config/model", provider="together").review(
        _request()
    )

    body = json.loads(seen[0].content)
    assert body["messages"] == [
        {"role": "system", "content": ENTITY_REVIEW_SYSTEM_PROMPT},
        {"role": "user", "content": review_user_message(_request())},
    ]
    assert body["response_format"]["json_schema"] == {
        "name": "entity_match_review",
        "schema": ENTITY_REVIEW_RESPONSE_SCHEMA,
    }
    assert result.confidence == 0.9


def _timeout(_: httpx.Request) -> httpx.Response:
    raise httpx.ReadTimeout("slow")


def _refused(_: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("refused")


@pytest.mark.parametrize(
    ("handler", "transient", "message"),
    [
        (_timeout, True, "LlmTimeoutError: Together AI request timed out after 5s"),
        (_refused, True, "LlmUnavailableError: Together AI is unreachable: ConnectError"),
        (
            lambda _: httpx.Response(429, text=SECRET),
            True,
            "LlmRateLimitError: Together AI returned HTTP 429",
        ),
        (
            lambda _: httpx.Response(503, text=SECRET),
            True,
            "LlmUnavailableError: Together AI returned HTTP 503",
        ),
        (
            lambda _: httpx.Response(401, text=SECRET),
            False,
            "LlmAuthenticationError: Together AI returned HTTP 401",
        ),
        (
            lambda _: httpx.Response(400, text=SECRET),
            False,
            "LlmRequestRejectedError: Together AI returned HTTP 400",
        ),
        (
            lambda _: httpx.Response(200, json=_envelope("{", "length")),
            False,
            "LlmInvalidResponseError: Together AI output was truncated (finish_reason=length)",
        ),
        (
            lambda _: httpx.Response(200, json=_envelope(SECRET)),
            False,
            "LlmInvalidResponseError: Together AI output is not valid JSON",
        ),
    ],
)
def test_the_review_marks_each_provider_failure(
    handler: Callable[[httpx.Request], httpx.Response], transient: bool, message: str
) -> None:
    error = _review(handler)

    assert error.transient is transient
    assert str(error) == message
    assert isinstance(error.__cause__, LlmError)
