"""The steps that ask a paid model say so, with what is left to spend."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from web.app import app
from web.dependencies import get_db
from web.ui import spend
from web.ui.cycle import _processing
from web.ui.pipeline import PipelineState, step_confirmation, stepper
from web.ui.workload import Workload

CREDITS = {"data": {"total_credits": 15, "total_usage": 10.737357613}}


def _paid(monkeypatch: pytest.MonkeyPatch, *, junk: str = "jev") -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.delenv("ENTITY_NORMALIZE_MODEL", raising=False)
    monkeypatch.delenv("ENTITY_MODEL_BUDGET_USD", raising=False)
    monkeypatch.setenv("JUNK_SCREEN", junk)


def _credits(monkeypatch: pytest.MonkeyPatch, answer: object = CREDITS) -> list[str]:
    asked: list[str] = []

    def request(key: str) -> object:
        asked.append(key)
        return answer

    monkeypatch.setattr(spend, "_request", request)
    spend.reset_cache()
    return asked


def test_the_balance_is_credits_bought_minus_credits_used(monkeypatch: pytest.MonkeyPatch) -> None:
    """Not the key's own limit: that one reads «89 left» on an account holding $4."""
    _paid(monkeypatch)
    _credits(monkeypatch)

    found = spend.balance()

    assert found is not None and round(found.remaining, 2) == 4.26
    assert (
        spend.balance_text() == "Остаток на OpenRouter: $4.26 (куплено $15.00, потрачено $10.74)."
    )


def test_the_balance_is_asked_once_a_minute_and_a_failure_does_not_slow_every_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paid(monkeypatch)
    asked = _credits(monkeypatch, None)
    clock = [0.0]

    for moment in (0.0, 10.0, 59.0):
        clock[0] = moment
        assert spend.balance(now=lambda: clock[0]) is None
    assert len(asked) == 1, "one question, and its failure is remembered for the minute"

    clock[0] = 61.0
    spend.balance(now=lambda: clock[0])
    assert len(asked) == 2


def test_no_key_means_no_question_and_no_words(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    asked = _credits(monkeypatch)

    assert spend.balance() is None and spend.balance_text() == ""
    assert spend.notice("figurants") == "" and spend.balance_line() == ""
    assert asked == []


@pytest.mark.parametrize(
    "answer",
    [None, {}, {"data": {}}, {"data": {"total_credits": "x", "total_usage": 1}}],
)
def test_an_answer_that_is_not_a_balance_says_it_could_not_be_known(
    monkeypatch: pytest.MonkeyPatch, answer: object
) -> None:
    _paid(monkeypatch)
    _credits(monkeypatch, answer)

    assert spend.balance_text() == "Баланс OpenRouter сейчас узнать не удалось."


def test_every_step_that_asks_a_model_warns_and_the_others_do_not(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paid(monkeypatch)
    _credits(monkeypatch)

    for stage in ("purge", "entities", "figurants", "political"):
        assert spend.paid(stage)
        assert "платный" in spend.notice(stage)
        assert "Остаток на OpenRouter: $4.26" in spend.confirm_text(stage)
    assert not spend.paid("load") and spend.notice("load") == ""
    assert spend.confirm_text("load") == "", "a free step is not told the balance"
    assert "около $0.00006 за статью" in spend.spend_text("purge")
    assert "не более $2.00 за запуск" in spend.spend_text("entities")


def test_the_purge_is_paid_only_while_the_screen_is_the_decision_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paid(monkeypatch, junk="1")
    assert not spend.paid("purge") and spend.notice("purge") == ""
    _paid(monkeypatch, junk="0")
    assert not spend.paid("purge")
    _paid(monkeypatch, junk="jev")
    monkeypatch.delenv("OPENROUTER_API_KEY")
    assert not spend.paid("purge"), "no key: the screen cannot ask, and costs nothing"


def test_the_purge_asks_for_the_deletion_and_for_the_money(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The destructive warning must not be lost behind the paid one."""
    _paid(monkeypatch)
    _credits(monkeypatch)

    text = step_confirmation("purge")

    assert "необратимо" in text and "JEV" in text and "Остаток на OpenRouter: $4.26" in text
    page = stepper(PipelineState("purge"), 0)
    assert "необратимо" in page and "JEV" in page and "return confirm" in page
    assert "⚠ Шаг платный" in page


def test_a_balance_under_the_limit_of_a_run_is_said_in_red(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paid(monkeypatch)
    _credits(monkeypatch, {"data": {"total_credits": 15, "total_usage": 14.0}})
    low = spend.notice("figurants")
    assert 'class="warning ai-note"' in low and "Остатка меньше лимита запуска" in low

    _credits(monkeypatch)
    assert 'class="muted ai-note"' in spend.notice("figurants")
    # The purge costs cents: its «limit» is no reason to alarm.
    _credits(monkeypatch, {"data": {"total_credits": 15, "total_usage": 14.99}})
    assert 'class="muted ai-note"' in spend.notice("purge")


def test_a_free_or_other_model_has_no_paid_notice(monkeypatch: pytest.MonkeyPatch) -> None:
    _paid(monkeypatch)
    monkeypatch.setenv("ENTITY_NORMALIZE_MODEL", "deepseek/deepseek-v4.1-flash:free")
    assert spend.notice("entities") == ""
    monkeypatch.setenv("ENTITY_NORMALIZE_MODEL", "qwen/qwen3-8b")
    assert spend.notice("political") == ""


def test_the_page_of_the_cycle_shows_the_balance(monkeypatch: pytest.MonkeyPatch) -> None:
    _paid(monkeypatch)
    _credits(monkeypatch)
    work = Workload(pairs=0, unclear_roles=0, unclear_verdicts=0)

    page = _processing(PipelineState("figurants"), work)

    assert "Остаток на OpenRouter: $4.26" in page
    assert "Шаг платный: DeepSeek через OpenRouter" in page


def _strip(session_factory: sessionmaker[Session]) -> str:
    def override() -> Iterator[Session]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override
    try:
        page = TestClient(app).get("/ui/about").text
    finally:
        app.dependency_overrides.pop(get_db, None)
    return page.split('<section class="status-strip"', 1)[1].split("</section>", 1)[0]


def test_the_balance_is_a_chip_in_the_strip_at_the_top_of_every_page(
    monkeypatch: pytest.MonkeyPatch, session_factory: sessionmaker[Session]
) -> None:
    _paid(monkeypatch)
    _credits(monkeypatch)

    strip = _strip(session_factory)

    assert '<span class="balance" title="Куплено $15.00, потрачено $10.74.' in strip
    assert "<small>Баланс OpenRouter</small><strong>$4.26</strong>" in strip
    # At the end of the line, after the figures of the base.
    assert strip.index("Последний запуск") < strip.index("Баланс OpenRouter")


def test_a_balance_under_the_limit_of_a_run_is_red_in_the_strip(
    monkeypatch: pytest.MonkeyPatch, session_factory: sessionmaker[Session]
) -> None:
    _paid(monkeypatch)
    _credits(monkeypatch, {"data": {"total_credits": 15, "total_usage": 14.0}})

    strip = _strip(session_factory)

    assert '<span class="balance low"' in strip and "<strong>$1.00</strong>" in strip
    assert "Остатка меньше" in strip


def test_the_strip_says_a_dash_when_openrouter_does_not_answer_and_nothing_without_a_key(
    monkeypatch: pytest.MonkeyPatch, session_factory: sessionmaker[Session]
) -> None:
    _paid(monkeypatch)
    _credits(monkeypatch, None)
    assert '<span class="balance unknown"' in _strip(session_factory)
    assert "<strong>—</strong>" in _strip(session_factory)

    monkeypatch.delenv("OPENROUTER_API_KEY")
    spend.reset_cache()
    assert "balance" not in _strip(session_factory)
    assert "<small>Публикации</small>" in _strip(session_factory)
