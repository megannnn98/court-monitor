"""The junk screen's second opinion: an article goes only when both Jevs say it may."""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence

import httpx
import pytest

from processing.junk.article_screen import JunkScreenError
from processing.junk.decision_screen import DECISIONS_URL, DecisionScreen
from processing.junk.drop_verifier import (
    COUNTS,
    ERROR,
    MIN_P_DROP,
    CascadeScreen,
    DropVerifier,
    Verdict,
    VerifierAnswerError,
    verdict_of,
)
from processing.junk.drop_verifier_prompt import DESCRIPTION, PROMPT_VERSION
from processing.junk.screen import screen_from_env

MODEL = "typesafe/jev-1.13"
CUTOFF = 0.5


class FixedScreen:
    """JEV-1 with its scores given: what it would answer is not under test here."""

    def __init__(self, scores: dict[str, float]) -> None:
        self._scores = scores

    @property
    def name(self) -> str:
        return "jev-screen-v1:test"

    @property
    def cutoff(self) -> float:
        return CUTOFF

    def scores(self, articles: Sequence[tuple[str, str]]) -> list[float]:
        return [self._scores[title] for title, _ in articles]


def _verifier_answer(choice: str, p_drop: float = 0.9) -> dict[str, object]:
    rest = round((1 - p_drop) / 2, 4)
    probabilities = {"DROP": p_drop, "KEEP": rest, "UNCERTAIN": rest}
    diagnostic = {"choice": "unclear", "probabilities": {"unclear": 1.0}}
    return {
        "model": f"{MODEL}-20260917",
        "answers": {
            "decision": {"choice": choice, "probabilities": probabilities, "confidence": 0.8},
            "subject": diagnostic,
            "character": diagnostic,
            "process": diagnostic,
        },
        "usage": {"cost": 0.0002},
    }


class Service:
    """The decisions endpoint as JEV-2 meets it: an answer per article title."""

    def __init__(self, answers: dict[str, httpx.Response | Exception]) -> None:
        self.answers = answers
        self.asked: list[str] = []
        self.bodies: list[dict[str, object]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert str(request.url) == DECISIONS_URL
        body = json.loads(request.content)
        self.bodies.append(body)
        record = body["state"]["records"][0]["record"]
        title = record.split("\n\n", 1)[0]
        self.asked.append(title)
        answer = self.answers[title]
        if isinstance(answer, Exception):
            raise answer
        return answer


def _cascade(scores: dict[str, float], service: Service) -> CascadeScreen:
    verifier = DropVerifier(
        lambda: httpx.Client(transport=httpx.MockTransport(service)), "key", sleep=0
    )
    return CascadeScreen(FixedScreen(scores), verifier)


def _ok(choice: str, p_drop: float = 0.9) -> httpx.Response:
    return httpx.Response(200, json=_verifier_answer(choice, p_drop))


def test_jev1_keep_passes_on_and_jev2_is_never_asked() -> None:
    service = Service({})
    cascade = _cascade({"Суд": 0.93, "Арест": 0.5}, service)

    assert cascade.scores([("Суд", "задержан активист"), ("Арест", "арестован")]) == [0.93, 0.5]
    assert service.asked == []
    assert cascade.counts["jev2_called"] == 0 and cascade.counts["jev1_keep"] == 2


def test_jev2_keep_rescues_an_article_jev1_would_drop() -> None:
    service = Service({"Пост": _ok("KEEP", p_drop=0.05)})
    cascade = _cascade({"Пост": 0.1}, service)

    [score] = cascade.scores([("Пост", "дело за репост")])

    assert score >= cascade.cutoff
    assert service.asked == ["Пост"]
    assert cascade.counts["jev2_keep_rescue"] == 1 and cascade.counts["final_keep"] == 1


def test_an_article_goes_only_when_both_drop_it() -> None:
    service = Service({"Погода": _ok("DROP", p_drop=0.95)})
    cascade = _cascade({"Погода": 0.02}, service)

    assert cascade.scores([("Погода", "дождь")]) == [0.02]
    assert cascade.counts["jev2_drop_confirmed"] == 1 and cascade.counts["final_drop"] == 1


@pytest.mark.parametrize(
    "answer",
    [
        _ok("UNCERTAIN", p_drop=0.4),
        # DROP, but not sure enough.
        _ok("DROP", p_drop=MIN_P_DROP - 0.01),
        httpx.TimeoutException("read timeout"),
        httpx.ConnectError("no route"),
        RuntimeError("anything at all"),
        httpx.Response(503),
        httpx.Response(401),
        httpx.Response(200, text="<html>"),
        httpx.Response(200, json={"model": MODEL, "answers": {}}),
        httpx.Response(200, json={"model": MODEL, "answers": {"decision": {"choice": "MAYBE"}}}),
        httpx.Response(200, json=_verifier_answer("DROP") | {"model": "deepseek/v4"}),
        httpx.Response(200, json=_verifier_answer("DROP") | {"answers": "DROP"}),
    ],
    ids=[
        "uncertain",
        "unsure-drop",
        "timeout",
        "connection",
        "exception",
        "server-error",
        "refused",
        "not-json",
        "no-decision",
        "unknown-choice",
        "another-model",
        "malformed",
    ],
)
def test_anything_but_a_sure_drop_of_jev2_keeps_the_article(
    answer: httpx.Response | Exception,
) -> None:
    """Fail open: JEV-2 never deletes by failing, and never stops the purge."""
    cascade = _cascade({"Пост": 0.01}, Service({"Пост": answer}))

    [score] = cascade.scores([("Пост", "текст")])

    assert score == cascade.cutoff


def test_a_failing_jev2_is_counted_and_logged(caplog: pytest.LogCaptureFixture) -> None:
    cascade = _cascade({"Пост": 0.01}, Service({"Пост": httpx.TimeoutException("slow")}))

    with caplog.at_level(logging.INFO, logger="monitoring"):
        cascade.scores([("Пост", "текст")])

    assert cascade.counts["jev2_error"] == 1 and cascade.counts["final_keep"] == 1
    [line] = [r for r in caplog.records if "event=junk_screen_cascade " in r.getMessage()]
    assert line.levelno == logging.WARNING
    assert "jev2_decision=ERROR" in line.getMessage() and "TimeoutException" in line.getMessage()


def test_a_failing_jev1_stops_the_purge_before_jev2_is_asked() -> None:
    class Broken(FixedScreen):
        def scores(self, articles: Sequence[tuple[str, str]]) -> list[float]:
            raise JunkScreenError("no answer")

    service = Service({})
    verifier = DropVerifier(
        lambda: httpx.Client(transport=httpx.MockTransport(service)), "key", sleep=0
    )
    with pytest.raises(JunkScreenError):
        CascadeScreen(Broken({}), verifier).scores([("Пост", "текст")])
    assert service.asked == []


def test_a_batch_lines_up_and_is_counted(caplog: pytest.LogCaptureFixture) -> None:
    service = Service(
        {
            "Погода": _ok("DROP", p_drop=0.97),
            "Пост": _ok("KEEP", p_drop=0.1),
            "Обрыв": httpx.Response(200, text="oops"),
        }
    )
    scores = {"Суд": 0.9, "Погода": 0.03, "Пост": 0.2, "Обрыв": 0.4}
    cascade = _cascade(scores, service)
    articles = [(title, "текст") for title in scores]

    with caplog.at_level(logging.INFO, logger="monitoring"):
        assert cascade.scores(articles) == [0.9, 0.03, CUTOFF, CUTOFF]

    assert sorted(service.asked) == ["Обрыв", "Погода", "Пост"]
    assert cascade.counts == {
        "total": 4,
        "jev1_keep": 1,
        "jev1_drop": 3,
        "jev2_called": 3,
        "jev2_keep_rescue": 2,
        "jev2_drop_confirmed": 1,
        "jev2_error": 1,
        "final_keep": 3,
        "final_drop": 1,
    }
    messages = [r.getMessage() for r in caplog.records]
    articles_logged = [m for m in messages if m.startswith("event=junk_screen_cascade ")]
    assert len(articles_logged) == 4
    assert any("jev2_called=no final=keep title='Суд'" in m for m in articles_logged)
    assert any(
        "jev1_score=0.0300" in m
        and "jev2_decision=DROP jev2_p_drop=0.9700" in m
        and "final=drop" in m
        for m in articles_logged
    )
    [batch] = [m for m in messages if m.startswith("event=junk_screen_cascade_batch")]
    assert all(f"{name}=" in batch for name in COUNTS)
    assert "final_drop=1" in batch


def test_jev2_reads_the_whole_article_and_the_rubric_never_jev1() -> None:
    service = Service({"Пост": _ok("KEEP")})
    _cascade({"Пост": 0.01}, service).scores([("Пост", "я" * 5000)])

    [body] = service.bodies
    assert body["model"] == MODEL
    assert body["state"]["records"] == [{"id": "article", "record": "Пост\n\n" + "я" * 5000}]  # type: ignore[index]
    assert body["state"]["description"] == DESCRIPTION  # type: ignore[index]
    # Nothing of JEV-1's answer travels: its score is nowhere in the request.
    assert "0.01" not in json.dumps(body)


def test_the_verifier_counts_its_cost_and_names_its_prompt() -> None:
    service = Service({"А": _ok("KEEP"), "Б": _ok("DROP")})
    verifier = DropVerifier(
        lambda: httpx.Client(transport=httpx.MockTransport(service)), "key", sleep=0
    )

    verifier.verdicts([("А", "x"), ("Б", "y")])

    assert verifier.cost_usd == pytest.approx(0.0004)
    assert PROMPT_VERSION in verifier.name and MODEL in verifier.name


def test_a_verdict_is_read_strictly() -> None:
    answers = _verifier_answer("DROP", p_drop=0.8)["answers"]
    assert verdict_of(answers) == Verdict(decision="DROP", p_drop=0.8, confidence=0.8)
    assert verdict_of(answers).confirms_drop
    assert not Verdict(decision="UNCERTAIN", p_drop=0.99).confirms_drop
    assert not Verdict(decision=ERROR).confirms_drop
    for broken in (
        None,
        {"decision": {"choice": "DROP", "probabilities": {"DROP": 0.9}}},
        {"decision": {"choice": "DROP", "probabilities": {"DROP": 2, "KEEP": 0, "UNCERTAIN": 0}}},
        {
            "decision": {
                "choice": "DROP",
                "probabilities": {"DROP": True, "KEEP": 0, "UNCERTAIN": 0},
            }
        },
    ):
        with pytest.raises(VerifierAnswerError):
            verdict_of(broken)


def _answer_with(monkeypatch: pytest.MonkeyPatch, transport: httpx.MockTransport) -> None:
    real = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda: real(transport=transport))


def _both(request: httpx.Request) -> httpx.Response:
    """The service for both Jevs: JEV-2's request has KEEP/DROP/UNCERTAIN criteria."""
    criteria = json.loads(request.content)["questions"]["decision"]["criteria"]
    if "DROP" in criteria:
        return _ok("KEEP")
    return httpx.Response(
        200,
        json={
            "model": MODEL,
            "answers": {"decision": {"probabilities": {"case": 0.1, "not_case": 0.9}}},
        },
    )


def test_the_verifier_is_off_unless_asked_for(monkeypatch: pytest.MonkeyPatch) -> None:
    _answer_with(monkeypatch, httpx.MockTransport(_both))

    alone = screen_from_env({"JUNK_SCREEN": "jev", "OPENROUTER_API_KEY": "key"})
    both = screen_from_env(
        {"JUNK_SCREEN": "jev", "JUNK_SCREEN_VERIFIER": "jev", "OPENROUTER_API_KEY": "key"}
    )

    assert isinstance(alone, DecisionScreen)
    assert isinstance(both, CascadeScreen) and both.cutoff == alone.cutoff


def test_a_verifier_that_does_not_answer_at_the_start_stops_the_purge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail open per article, but not blind: a dead JEV-2 would hold every article."""

    def handler(request: httpx.Request) -> httpx.Response:
        if "DROP" in json.loads(request.content)["questions"]["decision"]["criteria"]:
            return httpx.Response(401)
        return _both(request)

    _answer_with(monkeypatch, httpx.MockTransport(handler))

    with pytest.raises(JunkScreenError, match="drop verifier did not answer"):
        screen_from_env(
            {"JUNK_SCREEN": "jev", "JUNK_SCREEN_VERIFIER": "jev", "OPENROUTER_API_KEY": "key"}
        )
