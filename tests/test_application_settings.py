"""Startup configuration validation and redaction (no services)."""

from __future__ import annotations

import json

import pytest

from settings import ApplicationConfigurationError, ApplicationSettings

BASE = {"DATABASE_URL": "postgresql+psycopg://court:s3cret-pass@db:5432/court_monitor"}


def test_defaults_are_valid_with_a_database_url() -> None:
    settings = ApplicationSettings.from_env(BASE)

    assert settings.semantic.qdrant_url is None
    assert settings.embedding is None
    assert settings.together.configured is False
    assert settings.monitoring.cron == "0 * * * *"


def test_every_problem_is_reported_at_once() -> None:
    env = {
        "DATABASE_URL": "sqlite:///court.db",
        "DATABASE_POOL_SIZE": "0",
        "MONITORING_CRON": "hourly",
        "ER_AUTO_LINK_MIN_SCORE": "2",
        "SEMANTIC_CANDIDATE_POOL_SIZE": "100000",
        "TOGETHER_API_KEY": "key-only",
    }

    with pytest.raises(ApplicationConfigurationError) as raised:
        ApplicationSettings.from_env(env)

    problems = "\n".join(raised.value.problems)
    for fragment in (
        "PostgreSQL",
        "DATABASE_POOL_SIZE",
        "MONITORING_CRON",
        "ER_AUTO_LINK_MIN_SCORE",
        "SEMANTIC_CANDIDATE_POOL_SIZE",
        "TOGETHER_MODEL",
    ):
        assert fragment in problems
    assert "key-only" not in str(raised.value)


def test_missing_database_url_is_an_error_only_when_required() -> None:
    with pytest.raises(ApplicationConfigurationError, match="DATABASE_URL"):
        ApplicationSettings.from_env({})
    assert ApplicationSettings.from_env({}, require_database=False).database_url == ""


def test_uncalibrated_embedding_model_needs_an_explicit_threshold() -> None:
    env = {**BASE, "QDRANT_URL": "http://qdrant:6333", "EMBEDDING_MODEL_ID": "other/model"}

    with pytest.raises(ApplicationConfigurationError, match="SEMANTIC_DENSE_MIN_SCORE"):
        ApplicationSettings.from_env(env)

    settings = ApplicationSettings.from_env({**env, "SEMANTIC_DENSE_MIN_SCORE": "0.7"})
    assert settings.dense_min_score == 0.7


def test_redacted_configuration_has_no_secrets() -> None:
    env = {**BASE, "TOGETHER_API_KEY": "tgp_super_secret", "TOGETHER_MODEL": "some/model"}

    output = json.dumps(ApplicationSettings.from_env(env).redacted())

    assert "s3cret-pass" not in output
    assert "tgp_super_secret" not in output
    assert '"configured": true' in output
