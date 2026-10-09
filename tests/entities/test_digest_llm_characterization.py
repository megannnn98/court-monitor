"""Characterization of one model call end to end: the digest classifier over OpenRouter.

Pins the request (URL, auth, body, timeout), the structured answer, what each failure
becomes (HTTP, transport, a cut answer, a broken body, invalid JSON, a schema
violation), that nothing is retried, and what a call costs and logs. No network: an
httpx MockTransport answers.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable

import httpx
import pytest
from pydantic import ValidationError

from entities.digest import (
    _RESPONSE_SCHEMA,
    MAX_TOKENS,
    SYSTEM_PROMPT,
    DigestAnswer,
    DigestClassifierError,
    OpenRouterDigestClassifier,
)
from entities.llm import Spend
from monitor_core.llm import Endpoint, ModelError

URL = "https://openrouter.test/api/v1/chat/completions"
TITLES = {7: "Суд арестовал активиста", 9: "Главное за день"}


def _classifier(
    handler: Callable[[httpx.Request], httpx.Response],
) -> tuple[OpenRouterDigestClassifier, list[httpx.Request], Spend]:
    requests: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return handler(request)

    spend = Spend(budget_usd=1.0)
    classifier = OpenRouterDigestClassifier(
        "key",
        http_client=httpx.Client(transport=httpx.MockTransport(record)),
        endpoint=Endpoint("openrouter", URL, "deepseek/test", "key"),
        spend=spend,
    )
    return classifier, requests, spend


def _answer(content: object, *, finish_reason: object = "stop") -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [{"message": {"content": content}, "finish_reason": finish_reason}],
            "usage": {"prompt_tokens": 120, "completion_tokens": 30, "cost": 0.0015},
        },
    )


VALID = json.dumps(
    {
        "answers": [
            {"id": 7, "relevant": True, "explanation": "арест"},
            {"id": 9, "relevant": False, "explanation": "дайджест"},
            {"id": 99, "relevant": True, "explanation": "чужой id"},
        ]
    },
    ensure_ascii=False,
)


def test_request_and_structured_answer(caplog: pytest.LogCaptureFixture) -> None:
    classifier, requests, spend = _classifier(lambda _: _answer(VALID))

    with caplog.at_level(logging.INFO, logger="entities"):
        answers = classifier.classify(TITLES)

    assert answers == {
        7: DigestAnswer(id=7, relevant=True, explanation="арест"),
        9: DigestAnswer(id=9, relevant=False, explanation="дайджест"),
    }
    [request] = requests
    assert (request.method, str(request.url)) == ("POST", URL)
    assert request.headers["authorization"] == "Bearer key"
    assert request.extensions["timeout"] == {
        "connect": 180.0,
        "read": 180.0,
        "write": 180.0,
        "pool": 180.0,
    }
    assert json.loads(request.content) == {
        "model": "deepseek/test",
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": json.dumps(
                    [{"id": 7, "title": TITLES[7]}, {"id": 9, "title": TITLES[9]}],
                    ensure_ascii=False,
                ),
            },
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "digest", "strict": True, "schema": _RESPONSE_SCHEMA},
        },
        "temperature": 0,
        "max_tokens": MAX_TOKENS,
        "provider": {"require_parameters": True},
        "reasoning": {"enabled": False},
        "usage": {"include": True},
    }
    assert (spend.calls, round(spend.cost_usd, 6)) == (1, 0.0015)
    assert [record.getMessage() for record in caplog.records if record.name == "entities"] == [
        (
            "event=entity_model_call provider=openrouter model=deepseek/test prompt_tokens=120 "
            "completion_tokens=30 cost_usd=0.001500"
        ),
        "event=article_digest_classified model=deepseek/test asked=2 answered=2",
    ]


def test_a_local_endpoint_gets_no_openrouter_options_and_no_auth() -> None:
    requests: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return _answer(VALID)

    classifier = OpenRouterDigestClassifier(
        None,
        http_client=httpx.Client(transport=httpx.MockTransport(record)),
        endpoint=Endpoint("local", "http://127.0.0.1:11434/v1/chat/completions", "qwen", None),
    )

    classifier.classify(TITLES)

    body = json.loads(requests[0].content)
    assert set(body) == {"model", "messages", "response_format", "temperature", "max_tokens"}
    assert "authorization" not in requests[0].headers


def _failure(
    handler: Callable[[httpx.Request], httpx.Response],
) -> tuple[DigestClassifierError, list[httpx.Request], Spend]:
    classifier, requests, spend = _classifier(handler)
    with pytest.raises(DigestClassifierError) as caught:
        classifier.classify(TITLES)
    return caught.value, requests, spend


@pytest.mark.parametrize(
    ("status", "body"),
    [(503, "upstream down"), (429, "slow down"), (401, "bad key"), (400, "x" * 400)],
)
def test_an_http_error_is_one_call_with_the_provider_message(status: int, body: str) -> None:
    error, requests, spend = _failure(lambda _: httpx.Response(status, text=body))

    assert str(error) == f"HTTP {status}: {body[:300]}"
    assert type(error.__cause__) is ModelError
    assert error.__cause__.__cause__ is None
    assert len(requests) == 1
    assert spend.calls == 0


@pytest.mark.parametrize(
    ("exception", "kind"),
    [
        (httpx.ConnectError("refused"), "ConnectError"),
        (httpx.ReadTimeout("slow"), "ReadTimeout"),
    ],
)
def test_a_transport_failure_is_one_call(exception: Exception, kind: str) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        raise exception

    error, requests, spend = _failure(handler)

    assert str(error).startswith(f"{kind}: ")
    assert type(error.__cause__) is ModelError
    assert type(error.__cause__.__cause__) is type(exception)
    assert len(requests) == 1
    assert spend.calls == 0


def test_a_cut_answer_is_counted_then_refused() -> None:
    error, requests, spend = _failure(lambda _: _answer("{", finish_reason="length"))

    assert str(error) == "unusable answer: finish_reason=length"
    assert len(requests) == 1
    assert spend.calls == 1


def test_an_answer_without_text_is_refused() -> None:
    error, _, spend = _failure(lambda _: _answer(None))

    assert str(error) == "unusable answer: no content"
    assert spend.calls == 1


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (httpx.Response(200, text="not json"), "unusable answer: JSONDecodeError"),
        (httpx.Response(200, json={"choices": []}), "unusable answer: IndexError"),
        (httpx.Response(200, json={"choices": [{}]}), "unusable answer: KeyError"),
    ],
)
def test_a_broken_provider_body_is_refused_uncounted(
    response: httpx.Response, message: str
) -> None:
    error, _, spend = _failure(lambda _: response)

    assert str(error) == message
    assert type(error.__cause__) is ModelError
    assert spend.calls == 0


@pytest.mark.parametrize(
    "content",
    [
        "not json at all",
        json.dumps({"answers": [{"id": 7, "relevant": True}]}),
        json.dumps({"answers": [{"id": 7, "relevant": True, "explanation": "x" * 301}]}),
    ],
)
def test_invalid_json_or_a_schema_violation_is_refused_after_counting(content: str) -> None:
    error, requests, spend = _failure(lambda _: _answer(content))

    assert str(error) == "unusable answer: ValidationError"
    assert type(error.__cause__) is ValidationError
    assert len(requests) == 1
    assert spend.calls == 1
