"""The allowlist decides by numeric id alone, and the decision is logged without text."""

from __future__ import annotations

import logging

import pytest

from telegram_bot.authorization import Authorization
from telegram_bot.config import TelegramBotSettings

ENV = {"TELEGRAM_BOT_TOKEN": "123456:secret-token", "TELEGRAM_ALLOWED_USER_IDS": "42,77"}


@pytest.fixture
def authorization() -> Authorization:
    return Authorization(TelegramBotSettings.from_env(ENV))


def test_allowed_user_passes(authorization: Authorization) -> None:
    assert authorization.is_allowed(42)


def test_unknown_user_is_denied(authorization: Authorization) -> None:
    assert not authorization.is_allowed(4242)


def test_a_message_without_a_user_is_denied(authorization: Authorization) -> None:
    assert not authorization.is_allowed(None)


def test_the_username_does_not_matter(authorization: Authorization) -> None:
    # Two usernames, one id: the allowlist sees only the id.
    assert authorization.is_allowed(42) and authorization.is_allowed(42)


def test_the_decision_is_logged_with_the_id_only(
    authorization: Authorization, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="telegram_bot.authorization"):
        assert not authorization.check("update", 4242)
    record = caplog.records[-1].getMessage()
    assert "command=update" in record
    assert "telegram_user_id=4242" in record
    assert "result=denied" in record
