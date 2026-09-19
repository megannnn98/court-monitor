"""Telegram bot configuration: fail-fast on every field, and a redacted view."""

from __future__ import annotations

import pytest

from sources.source_registry import SOURCES, SourceKind, news_source_base_urls
from telegram_bot.config import REDACTED, TelegramBotSettings, TelegramConfigurationError

VALID = {
    "TELEGRAM_BOT_TOKEN": "123456:secret-token",
    "TELEGRAM_ALLOWED_USER_IDS": "123456789,987654321",
}


def test_valid_configuration_has_defaults() -> None:
    settings = TelegramBotSettings.from_env(VALID)
    assert settings.allowed_user_ids == frozenset({123456789, 987654321})
    assert str(settings.timezone) == "Europe/Moscow"
    assert settings.people_default_limit == 50
    assert settings.people_max_limit == 200


def test_missing_token_is_rejected() -> None:
    with pytest.raises(TelegramConfigurationError) as error:
        TelegramBotSettings.from_env({"TELEGRAM_ALLOWED_USER_IDS": "1"})
    assert error.value.problems == ["TELEGRAM_BOT_TOKEN environment variable is not set"]


def test_empty_allowlist_is_rejected() -> None:
    with pytest.raises(TelegramConfigurationError) as error:
        TelegramBotSettings.from_env({**VALID, "TELEGRAM_ALLOWED_USER_IDS": "  "})
    assert "TELEGRAM_ALLOWED_USER_IDS is empty" in error.value.problems[0]


def test_non_numeric_user_id_is_rejected() -> None:
    with pytest.raises(TelegramConfigurationError) as error:
        TelegramBotSettings.from_env({**VALID, "TELEGRAM_ALLOWED_USER_IDS": "123,@someone"})
    assert error.value.problems == ["TELEGRAM_ALLOWED_USER_IDS has a non-numeric id: '@someone'"]


def test_unknown_timezone_is_rejected() -> None:
    with pytest.raises(TelegramConfigurationError) as error:
        TelegramBotSettings.from_env({**VALID, "TELEGRAM_BOT_TIMEZONE": "Mars/Olympus"})
    assert error.value.problems == ["TELEGRAM_BOT_TIMEZONE is not a known timezone: 'Mars/Olympus'"]


def test_known_timezone_is_accepted() -> None:
    settings = TelegramBotSettings.from_env({**VALID, "TELEGRAM_BOT_TIMEZONE": "Asia/Almaty"})
    assert str(settings.timezone) == "Asia/Almaty"


@pytest.mark.parametrize("value", ["0", "-1", "abc"])
def test_invalid_limit_is_rejected(value: str) -> None:
    with pytest.raises(TelegramConfigurationError):
        TelegramBotSettings.from_env({**VALID, "TELEGRAM_PEOPLE_DEFAULT_LIMIT": value})


def test_default_limit_above_max_is_rejected() -> None:
    with pytest.raises(TelegramConfigurationError) as error:
        TelegramBotSettings.from_env(
            {**VALID, "TELEGRAM_PEOPLE_DEFAULT_LIMIT": "300", "TELEGRAM_PEOPLE_MAX_LIMIT": "200"}
        )
    assert error.value.problems == [
        "TELEGRAM_PEOPLE_DEFAULT_LIMIT must not be greater than TELEGRAM_PEOPLE_MAX_LIMIT"
    ]


def test_every_problem_is_reported_at_once() -> None:
    with pytest.raises(TelegramConfigurationError) as error:
        TelegramBotSettings.from_env({"TELEGRAM_BOT_TIMEZONE": "Mars/Olympus"})
    assert len(error.value.problems) == 3


def test_redacted_configuration_hides_the_token() -> None:
    redacted = TelegramBotSettings.from_env(VALID).redacted()
    assert redacted["token"] == REDACTED
    assert "secret-token" not in str(redacted)
    assert redacted["allowed_user_ids_count"] == 2


def test_the_registry_of_persecuted_people_is_not_a_news_source() -> None:
    assert SOURCES["memopzk-figurants"].kind is SourceKind.REGISTRY
    assert SOURCES["memopzk-figurants"].base_url not in news_source_base_urls()


def test_news_sources_carry_the_news_kind() -> None:
    for name in ("ovd-info", "sota-vision", "kommersant", "sudrf-2zovs"):
        assert SOURCES[name].kind is SourceKind.NEWS
        assert SOURCES[name].base_url in news_source_base_urls()
