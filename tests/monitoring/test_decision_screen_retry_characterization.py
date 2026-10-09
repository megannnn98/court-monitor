"""Characterization of the decision screen's retry loop (`DecisionScreen._ask`).

`RETRIES` = 3 retries: at most 4 requests. A network error or HTTP 429/500/502/503/504
is retried after `sleep` x 1, x 2, x 3 seconds, never before the first request. Any
other status, or a 200 that is not JSON, fails at once. After the last retryable
failure a new `JunkScreenError` names it, chained to nothing. Only an answer is priced.
"""

from __future__ import annotations

import time
from collections.abc import Callable

import httpx
import pytest

from monitoring.article_screen import JunkScreenError
from monitoring.decision_screen import RETRIES, DecisionScreen

ANSWER = {
    "answers": {"decision": {"probabilities": {"case": 0.8, "not_case": 0.2}}},
    "usage": {"cost": 0.01},
}
REQUEST = {"model": "m"}


@pytest.fixture
def slept(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    sleeps: list[float] = []
    monkeypatch.setattr(time, "sleep", sleeps.append)
    return sleeps


def _ask(
    outcomes: list[httpx.Response | Exception],
) -> tuple[Callable[[], object], DecisionScreen, list[int]]:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        outcome = outcomes[len(calls) - 1]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    screen = DecisionScreen(lambda: httpx.Client(), "key", sleep=2.0)
    http = httpx.Client(transport=httpx.MockTransport(handler))
    return (lambda: screen._ask(http, REQUEST)), screen, calls


def test_retries_is_three() -> None:
    assert RETRIES == 3


@pytest.mark.parametrize(
    "failure",
    [
        httpx.Response(429),
        httpx.Response(500),
        httpx.Response(502),
        httpx.Response(503),
        httpx.Response(504),
        httpx.ConnectError("refused"),
        httpx.ReadTimeout("slow"),
    ],
)
def test_a_retryable_failure_is_asked_again(
    failure: httpx.Response | Exception, slept: list[float]
) -> None:
    ask, screen, calls = _ask([failure, failure, httpx.Response(200, json=ANSWER)])

    assert ask() == ANSWER
    assert (len(calls), slept) == (3, [2.0, 4.0])
    assert screen.cost_usd == 0.01


@pytest.mark.parametrize(
    ("failure", "named"),
    [
        (httpx.Response(503), "HTTP 503"),
        (httpx.Response(429), "HTTP 429"),
        (httpx.ConnectError("refused"), "ConnectError: refused"),
    ],
)
def test_after_four_requests_a_new_error_names_the_last_failure(
    failure: httpx.Response | Exception, named: str, slept: list[float]
) -> None:
    ask, screen, calls = _ask([failure] * 5)

    with pytest.raises(JunkScreenError) as caught:
        ask()

    assert len(calls) == 4
    assert slept == [2.0, 4.0, 6.0]
    assert str(caught.value) == f"The decision model did not answer the junk screen: {named}"
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert screen.cost_usd == 0.0


def test_the_last_failure_named_is_the_last_one(slept: list[float]) -> None:
    ask, _, _ = _ask(
        [httpx.Response(503), httpx.ConnectError("x"), httpx.Response(429), httpx.Response(502)]
    )

    with pytest.raises(JunkScreenError, match="answer the junk screen: HTTP 502$"):
        ask()


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422, 501])
def test_another_status_fails_at_once(status: int, slept: list[float]) -> None:
    ask, _, calls = _ask([httpx.Response(status), httpx.Response(200, json=ANSWER)])

    with pytest.raises(JunkScreenError) as caught:
        ask()

    assert str(caught.value) == f"The decision model refused the junk screen: HTTP {status}"
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert (len(calls), slept) == (1, [])


def test_an_answer_that_is_not_json_fails_at_once(slept: list[float]) -> None:
    ask, screen, calls = _ask(
        [httpx.Response(503), httpx.Response(200, text="<html>"), httpx.Response(200, json=ANSWER)]
    )

    with pytest.raises(JunkScreenError) as caught:
        ask()

    assert str(caught.value) == "The decision model's answer is not JSON"
    assert isinstance(caught.value.__cause__, ValueError)
    assert (len(calls), slept, screen.cost_usd) == (2, [2.0], 0.0)
