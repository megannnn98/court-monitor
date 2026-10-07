"""The junk screen that asks a decision model: what it scores, and when it stops the purge."""

from __future__ import annotations

import json

import httpx
import pytest

from monitoring import decision_screen
from monitoring.article_screen import JunkScreenError
from monitoring.decision_screen import (
    ARTICLE_CHARS,
    CUTOFF,
    DECISIONS_URL,
    DecisionScreen,
    decision_screen_from_env,
)
from monitoring.junk_screen import screen_from_env


def _answer(case: float) -> dict[str, object]:
    return {
        "model": "typesafe/jev-1.13",
        "answers": {
            "decision": {
                "type": "choice",
                "choice": "case" if case >= CUTOFF else "not_case",
                "probabilities": {"case": case, "not_case": round(1 - case, 4)},
            }
        },
    }


def _screen(handler: httpx.MockTransport) -> DecisionScreen:
    return DecisionScreen(lambda: httpx.Client(transport=handler), "secret-key", sleep=0)


def test_the_score_is_the_probability_of_a_case_in_the_order_asked() -> None:
    """A batch is asked concurrently; the scores must still line up with the articles."""
    asked: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == DECISIONS_URL
        assert request.headers["authorization"] == "Bearer secret-key"
        body = json.loads(request.content)
        asked.append(body)
        record = body["state"]["records"][0]["record"]
        return httpx.Response(200, json=_answer(0.93 if "задержан" in record else 0.02))

    screen = _screen(httpx.MockTransport(handler))
    articles = [("Погода", "дождь"), ("Суд", "задержан активист"), ("Спорт", "матч")] * 5

    assert screen.scores(articles) == [0.02, 0.93, 0.02] * 5
    assert screen.scores([]) == []
    # The cutoff the measurement was made at; moving it changes what is held and deleted.
    assert screen.cutoff == CUTOFF == 0.5
    assert screen.name == "jev-screen-v1:~typesafe/jev-latest"
    question = asked[0]["questions"]["decision"]  # type: ignore[index]
    assert question["type"] == "choice" and set(question["criteria"]) == {"case", "not_case"}


def test_only_the_start_of_an_article_is_sent() -> None:
    """As measured: the title and the first 1 500 characters."""
    sent: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content)["state"]["records"][0]["record"])
        return httpx.Response(200, json=_answer(0.1))

    _screen(httpx.MockTransport(handler)).scores([("Заголовок", "я" * 5000)])

    assert sent == ["Заголовок. " + "я" * ARTICLE_CHARS]


def test_a_service_that_recovers_is_asked_again() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls < 3:
            return httpx.Response(503)
        return httpx.Response(200, json=_answer(0.7))

    assert _screen(httpx.MockTransport(handler)).scores([("Суд", "арест")]) == [0.7]
    assert calls == 3


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (httpx.Response(503), "did not answer"),
        (httpx.Response(401, json={"error": "bad key"}), "refused"),
        (httpx.Response(200, text="<html>"), "not JSON"),
        (httpx.Response(200, json={"answers": {}}), "no probabilities"),
        (
            httpx.Response(200, json={"answers": {"decision": {"probabilities": {"case": 1}}}}),
            "no probabilities",
        ),
        (
            httpx.Response(
                200,
                json={"answers": {"decision": {"probabilities": {"case": 7, "not_case": 0}}}},
            ),
            "out of range",
        ),
    ],
)
def test_an_article_the_model_could_not_judge_stops_the_purge(
    response: httpx.Response, message: str
) -> None:
    """Never a guessed zero: a score of 0 would delete the article."""
    screen = _screen(httpx.MockTransport(lambda _request: response))

    with pytest.raises(JunkScreenError, match=message):
        screen.scores([("Суд", "арест")])


def test_a_refused_key_is_not_asked_again() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(401)

    with pytest.raises(JunkScreenError):
        _screen(httpx.MockTransport(handler)).scores([("Суд", "арест")])
    assert calls == 1


def test_a_connection_that_fails_stops_the_purge() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    with pytest.raises(JunkScreenError, match="ConnectError"):
        _screen(httpx.MockTransport(handler)).scores([("Суд", "арест")])


def _answer_with(monkeypatch: pytest.MonkeyPatch, transport: httpx.MockTransport) -> None:
    """The client `decision_screen_from_env` makes talks to `transport`, not the network."""
    real = httpx.Client
    monkeypatch.setattr(decision_screen.httpx, "Client", lambda: real(transport=transport))


def test_turned_on_the_decision_screen_is_tried_before_the_purge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`JUNK_SCREEN=jev` picks the decision model, and one question proves it answers."""
    asked: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(request.headers["authorization"])
        return httpx.Response(200, json=_answer(0.0))

    _answer_with(monkeypatch, httpx.MockTransport(handler))

    screen = screen_from_env({"JUNK_SCREEN": "jev", "OPENROUTER_API_KEY": " key "})

    assert isinstance(screen, DecisionScreen) and asked == ["Bearer key"]


def test_the_decision_screen_without_a_key_or_an_answer_fails_before_the_purge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(JunkScreenError, match="OPENROUTER_API_KEY"):
        decision_screen_from_env({"JUNK_SCREEN": "jev"})

    _answer_with(monkeypatch, httpx.MockTransport(lambda _request: httpx.Response(401)))
    with pytest.raises(JunkScreenError, match="refused"):
        screen_from_env({"JUNK_SCREEN": "jev", "OPENROUTER_API_KEY": "key"})


def test_the_screen_counts_what_its_answers_cost() -> None:
    priced = iter([0.00004, 0.00006, None])

    def handler(request: httpx.Request) -> httpx.Response:
        answer = _answer(0.9)
        cost = next(priced)
        if cost is not None:
            answer["usage"] = {"input_tokens": 900, "output_tokens": 30, "cost": cost}
        return httpx.Response(200, json=answer)

    screen = _screen(httpx.MockTransport(handler))
    assert screen.cost_usd == 0

    screen.scores([("Суд", "приговор")])
    screen.scores([("Суд", "арест")])
    # An answer without a price is still an answer: it adds nothing.
    screen.scores([("Суд", "обыск")])

    assert screen.cost_usd == pytest.approx(0.0001)
