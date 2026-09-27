"""Startup configuration validation and redaction (no services)."""

from __future__ import annotations

import json

import pytest

from settings import ApplicationConfigurationError, ApplicationSettings

BASE = {"DATABASE_URL": "postgresql+psycopg://court:s3cret-pass@db:5432/court_monitor"}


def test_defaults_are_valid_with_a_database_url() -> None:
    settings = ApplicationSettings.from_env(BASE)

    assert settings.together.configured is False
    assert settings.monitoring.cron == "0 * * * *"


def test_every_problem_is_reported_at_once() -> None:
    env = {
        "DATABASE_URL": "sqlite:///court.db",
        "DATABASE_POOL_SIZE": "0",
        "MONITORING_CRON": "hourly",
        "ER_AUTO_LINK_MIN_SCORE": "2",
        "TOGETHER_API_KEY": "key-only",
        "TELEGRAM_HISTORY_DAYS": "not-a-number",
    }

    with pytest.raises(ApplicationConfigurationError) as raised:
        ApplicationSettings.from_env(env)

    problems = "\n".join(raised.value.problems)
    for fragment in (
        "PostgreSQL",
        "DATABASE_POOL_SIZE",
        "MONITORING_CRON",
        "ER_AUTO_LINK_MIN_SCORE",
        "TOGETHER_MODEL",
        "TELEGRAM_HISTORY_DAYS",
    ):
        assert fragment in problems
    assert "key-only" not in str(raised.value)


def test_a_negative_telegram_history_window_is_a_startup_error() -> None:
    assert ApplicationSettings.from_env(BASE).telegram_history_days == 30

    with pytest.raises(ApplicationConfigurationError, match="TELEGRAM_HISTORY_DAYS"):
        ApplicationSettings.from_env({**BASE, "TELEGRAM_HISTORY_DAYS": "-5"})


def test_missing_database_url_is_an_error_only_when_required() -> None:
    with pytest.raises(ApplicationConfigurationError, match="DATABASE_URL"):
        ApplicationSettings.from_env({})
    assert ApplicationSettings.from_env({}, require_database=False).database_url == ""


def test_redacted_configuration_has_no_secrets() -> None:
    env = {**BASE, "TOGETHER_API_KEY": "tgp_super_secret", "TOGETHER_MODEL": "some/model"}

    output = json.dumps(ApplicationSettings.from_env(env).redacted())

    assert "s3cret-pass" not in output
    assert "tgp_super_secret" not in output
    assert '"configured": true' in output
    assert '"telegram_history_days": 30' in output
