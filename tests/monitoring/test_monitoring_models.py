"""Monitoring settings and typed failure classification (no services)."""

from __future__ import annotations

from datetime import timedelta

import httpx
import pytest
from sqlalchemy.exc import IntegrityError, OperationalError

from monitoring.models import (
    FailureKind,
    MonitoringConfigurationError,
    MonitoringSettings,
    classify_failure,
)
from semantic_retrieval.models import IndexModelMismatchError, RetrievalUnavailableError
from sources.ingestion_errors import (
    ParseError,
    PermanentDiscoveryError,
    PermanentFetchError,
    PersistenceError,
    TransientDiscoveryError,
    TransientFetchError,
)
from sources.source_registry import SOURCES


def test_settings_defaults_are_bounded_and_hourly() -> None:
    settings = MonitoringSettings.from_env({})

    assert settings.enabled_sources == tuple(SOURCES)
    assert {"ovd-info", "sota-vision", "tg-ovdinfolive", "tg-moscowcourts"} <= set(
        settings.enabled_sources
    )
    assert MonitoringSettings.from_env({"MONITORING_ENABLED_SOURCES": " "}).enabled_sources == (
        tuple(SOURCES)
    )
    assert settings.cron == "0 * * * *"
    assert settings.discovery_limit == 50
    assert settings.stale_run_after == timedelta(minutes=120)


def test_settings_read_env() -> None:
    settings = MonitoringSettings.from_env(
        {
            "MONITORING_ENABLED_SOURCES": " sota-vision , ",
            "MONITORING_CRON": "*/30 * * * *",
            "MONITORING_DISCOVERY_LIMIT": "5",
            "MONITORING_STALE_RUN_AFTER_MINUTES": "45",
        }
    )

    assert settings.enabled_sources == ("sota-vision",)
    assert settings.cron == "*/30 * * * *"
    assert settings.discovery_limit == 5
    assert settings.stale_run_after == timedelta(minutes=45)


@pytest.mark.parametrize(
    "env",
    [
        {"MONITORING_ENABLED_SOURCES": "ovd-info,unknown"},
        {"MONITORING_DISCOVERY_LIMIT": "0"},
        {"MONITORING_DISCOVERY_LIMIT": "many"},
        {"MONITORING_STALE_RUN_AFTER_MINUTES": "0"},
        {"MONITORING_CRON": "hourly"},
    ],
)
def test_settings_reject_invalid_values(env: dict[str, str]) -> None:
    with pytest.raises(MonitoringConfigurationError):
        MonitoringSettings.from_env(env)


@pytest.mark.parametrize(
    ("error", "kind"),
    [
        (TransientFetchError("timeout"), FailureKind.RETRYABLE),
        (TransientDiscoveryError("HTTP 503"), FailureKind.RETRYABLE),
        (PersistenceError("db"), FailureKind.RETRYABLE),
        (OperationalError("select 1", {}, Exception("connection refused")), FailureKind.RETRYABLE),
        (httpx.ConnectTimeout("timeout"), FailureKind.RETRYABLE),
        (RetrievalUnavailableError("qdrant down"), FailureKind.RETRYABLE),
        (IndexModelMismatchError("other model"), FailureKind.NON_RETRYABLE),
        (PermanentFetchError("HTTP 404"), FailureKind.NON_RETRYABLE),
        (PermanentDiscoveryError("HTTP 403"), FailureKind.NON_RETRYABLE),
        (ParseError("no title"), FailureKind.NON_RETRYABLE),
        (IntegrityError("insert", {}, Exception("duplicate")), FailureKind.NON_RETRYABLE),
        (ValueError("bad"), FailureKind.NON_RETRYABLE),
    ],
)
def test_failure_kind_is_decided_by_exception_type(error: Exception, kind: FailureKind) -> None:
    assert classify_failure(error) is kind


def test_failure_kind_ignores_message_text() -> None:
    assert classify_failure(ValueError("temporary network timeout")) is FailureKind.NON_RETRYABLE
