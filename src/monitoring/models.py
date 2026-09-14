"""Monitoring domain types (ADR 0013). No Dagster imports here."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

import httpx
from pydantic import BaseModel, Field
from sqlalchemy.exc import DBAPIError, OperationalError

from semantic_retrieval.models import IndexModelMismatchError, RetrievalUnavailableError
from sources.ingestion_errors import PersistenceError, TransientDiscoveryError, TransientFetchError
from sources.source_registry import SOURCES

DEFAULT_ENABLED_SOURCES = ("ovd-info", "sota-vision")
DEFAULT_CRON = "0 * * * *"
# Regular monitoring only looks at the newest listing pages; full history is an explicit backfill.
DEFAULT_DISCOVERY_LIMIT = 50
# A normal run takes minutes; a run silent this long has lost its process.
DEFAULT_STALE_RUN_AFTER_MINUTES = 120
DERIVED_SCOPE = "derived"


class MonitoringConfigurationError(ValueError):
    pass


class MonitoringRunStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    COMPLETED_WITH_ERRORS = "completed_with_errors"
    FAILED = "failed"
    # Recovered after its process died (no heartbeat within the stale timeout).
    ABORTED = "aborted"


class MonitoringTrigger(StrEnum):
    SCHEDULE = "schedule"
    MANUAL = "manual"
    BACKFILL = "backfill"
    DERIVED = "derived"


class MonitoringStage(StrEnum):
    DISCOVERY = "discovery"
    INGESTION = "ingestion"
    EXTRACTION = "extraction"
    RESOLUTION = "resolution"
    CLASSIFICATION = "classification"
    RF_MATCHING = "rf_matching"
    SEMANTIC_INDEXING = "semantic_indexing"
    FINDINGS = "findings"


class FailureKind(StrEnum):
    RETRYABLE = "retryable"
    NON_RETRYABLE = "non_retryable"


class MonitoringAlreadyRunningError(RuntimeError):
    def __init__(self, scope: str, running_run_id: int | None) -> None:
        super().__init__(f"Monitoring {scope} is already running (run {running_run_id})")
        self.scope = scope
        self.running_run_id = running_run_id


def source_scope(source: str) -> str:
    return f"source:{source}"


def classify_failure(error: BaseException) -> FailureKind:
    """Retryability by exception type only, never by message text.

    ER REVIEW, missing RF snapshots and invalid content are domain outcomes and
    never reach this function as retryable errors.
    """
    if isinstance(error, IndexModelMismatchError):
        return FailureKind.NON_RETRYABLE
    retryable = (
        TransientFetchError,
        TransientDiscoveryError,
        PersistenceError,
        OperationalError,
        httpx.TransportError,
        RetrievalUnavailableError,
        ConnectionError,
        TimeoutError,
    )
    if isinstance(error, retryable):
        return FailureKind.RETRYABLE
    if isinstance(error, DBAPIError) and error.connection_invalidated:
        return FailureKind.RETRYABLE
    return FailureKind.NON_RETRYABLE


def _positive_int(env: Mapping[str, str], name: str, default: int) -> int:
    raw = (env.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise MonitoringConfigurationError(f"{name} must be an integer") from exc
    if value < 1:
        raise MonitoringConfigurationError(f"{name} must be greater than 0")
    return value


@dataclass(frozen=True)
class MonitoringSettings:
    enabled_sources: tuple[str, ...] = DEFAULT_ENABLED_SOURCES
    cron: str = DEFAULT_CRON
    discovery_limit: int = DEFAULT_DISCOVERY_LIMIT
    stale_run_after: timedelta = timedelta(minutes=DEFAULT_STALE_RUN_AFTER_MINUTES)

    def __post_init__(self) -> None:
        unknown = sorted(set(self.enabled_sources) - set(SOURCES))
        if unknown:
            raise MonitoringConfigurationError(
                f"MONITORING_ENABLED_SOURCES has unknown sources: {', '.join(unknown)}"
            )
        if len(self.cron.split()) != 5:
            raise MonitoringConfigurationError("MONITORING_CRON must have 5 cron fields")

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> MonitoringSettings:
        env = os.environ if env is None else env
        raw_sources = env.get("MONITORING_ENABLED_SOURCES")
        sources = (
            DEFAULT_ENABLED_SOURCES
            if raw_sources is None
            else tuple(name.strip() for name in raw_sources.split(",") if name.strip())
        )
        return cls(
            enabled_sources=sources,
            cron=(env.get("MONITORING_CRON") or DEFAULT_CRON).strip(),
            discovery_limit=_positive_int(
                env, "MONITORING_DISCOVERY_LIMIT", DEFAULT_DISCOVERY_LIMIT
            ),
            stale_run_after=timedelta(
                minutes=_positive_int(
                    env, "MONITORING_STALE_RUN_AFTER_MINUTES", DEFAULT_STALE_RUN_AFTER_MINUTES
                )
            ),
        )


class MonitoringRunView(BaseModel):
    id: int
    scope: str
    source: str | None
    trigger_type: MonitoringTrigger
    status: MonitoringRunStatus
    parameters: dict[str, Any] = Field(default_factory=dict)
    started_at: datetime
    heartbeat_at: datetime
    finished_at: datetime | None
    duration_seconds: float | None
    documents_discovered: int
    documents_ingested: int
    documents_skipped: int
    documents_failed: int
    articles_extracted: int
    events_created: int
    persons_created: int
    persons_linked: int
    person_reviews_created: int
    classifications_created: int
    rf_matches_created: int
    semantic_entities_indexed: int
    findings_created: int
    error_count: int
    rf_snapshot_id: int | None
    stage_metrics: dict[str, Any] = Field(default_factory=dict)
    error_message: str | None


class MonitoringRunItemView(BaseModel):
    id: int
    run_id: int
    stage: MonitoringStage
    entity_type: str
    entity_id: int | None
    external_ref: str | None
    status: str
    failure_kind: FailureKind
    error_type: str
    error_message: str
    created_at: datetime


class SourceMonitoringStateView(BaseModel):
    source_name: str
    last_successful_run_id: int | None
    last_successful_run_at: datetime | None
    last_discovered_at: datetime | None
    last_discovered_count: int
    last_external_marker: str | None
    updated_at: datetime


class MonitoringRunDetails(BaseModel):
    run: MonitoringRunView
    items: list[MonitoringRunItemView] = Field(default_factory=list)


class MonitoringStatusView(BaseModel):
    running: list[MonitoringRunView] = Field(default_factory=list)
    latest_runs: list[MonitoringRunView] = Field(default_factory=list)
    sources: list[SourceMonitoringStateView] = Field(default_factory=list)
    active_findings: int = 0
