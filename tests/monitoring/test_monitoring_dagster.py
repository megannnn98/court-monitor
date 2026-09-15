"""Dagster definitions executed in process (no daemon, no webserver)."""

from __future__ import annotations

from typing import Any, cast

import dagster as dg
import pytest
from qdrant_client import QdrantClient
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker
from support.monitoring_fixtures import SIDOROV, FakeUpstream, build_service, semantic_indexer

from monitoring.dagster.definitions import build_definitions
from monitoring.dagster.jobs import MONITORING_DERIVED_JOB, MONITORING_JOB, SOURCE_TAG
from monitoring.models import MonitoringRunStatus, MonitoringSettings, MonitoringTrigger
from monitoring.service import RunHandle, StageResult
from semantic_retrieval.models import RetrievalUnavailableError
from semantic_retrieval.vector_store import QdrantVectorStore, VectorStore
from sources.source_registry import SOURCES

EXPECTED_PARENTS = {
    "source_discovery": set(),
    "source_ingestion": {"source_discovery"},
    "entity_extraction": {"source_ingestion"},
    "person_resolution": {"entity_extraction"},
    "persecution_classification": {"person_resolution"},
    "rosfinmonitoring_matching": {"persecution_classification"},
    "semantic_indexing": {"rosfinmonitoring_matching"},
    "monitoring_summary": {"semantic_indexing"},
}


def _run_config(source: str = "ovd-info") -> dict[str, object]:
    return {"ops": {"source_discovery": {"config": {"source": source}}}}


def _metadata(result: dg.ExecuteInProcessResult, node: str) -> dict[str, object]:
    [materialization] = result.asset_materializations_for_node(node)
    return {key: value.value for key, value in materialization.metadata.items()}


def test_asset_graph_orders_stages_and_jobs_and_schedules_exist() -> None:
    settings = MonitoringSettings(cron="*/30 * * * *")
    defs = build_definitions(settings)

    graph = defs.resolve_asset_graph()
    parents = {
        key.to_user_string(): {parent.to_user_string() for parent in graph.get(key).parent_keys}
        for key in graph.get_all_asset_keys()
    }
    assert parents == EXPECTED_PARENTS
    assert {job.name for job in defs.resolve_all_job_defs()} >= {
        MONITORING_JOB,
        MONITORING_DERIVED_JOB,
    }
    schedules = {schedule.name: schedule for schedule in defs.schedules or []}
    assert set(schedules) == {
        f"monitoring_{source.replace('-', '_')}_schedule" for source in SOURCES
    }
    schedule = schedules["monitoring_ovd_info_schedule"]
    assert isinstance(schedule, dg.ScheduleDefinition)
    assert schedule.cron_schedule == "*/30 * * * *"
    assert schedule.tags == {SOURCE_TAG: "ovd-info"}
    assert schedule.default_status is dg.DefaultScheduleStatus.STOPPED


def test_monitoring_job_runs_all_stages_and_publishes_metadata(
    session_factory: sessionmaker[Session],
) -> None:
    upstream = FakeUpstream()
    upstream.publish("sidorov", SIDOROV)
    service = build_service(session_factory, {"ovd-info": upstream})
    defs = build_definitions(service.settings, monitoring=service)

    result = defs.resolve_job_def(MONITORING_JOB).execute_in_process(run_config=_run_config())

    assert result.success
    ingestion = _metadata(result, "source_ingestion")
    assert (ingestion["created"], ingestion["failed"]) == (1, 0)
    resolution = _metadata(result, "person_resolution")
    assert (resolution["created"], resolution["reviews"]) == (1, 0)
    summary = _metadata(result, "monitoring_summary")
    assert summary["status"] == "completed"
    assert summary["documents_ingested"] == 1
    assert summary["persons_created"] == 1
    run = service.repository.get_run(int(str(summary["monitoring_run_id"])))
    assert run is not None
    assert run.status is MonitoringRunStatus.COMPLETED
    assert run.trigger_type is MonitoringTrigger.MANUAL


def test_monitoring_job_skips_a_source_that_is_already_running(
    session_factory: sessionmaker[Session],
) -> None:
    upstream = FakeUpstream()
    upstream.publish("sidorov", SIDOROV)
    service = build_service(session_factory, {"ovd-info": upstream})
    running = service.start_source_run("ovd-info")
    defs = build_definitions(service.settings, monitoring=service)

    result = defs.resolve_job_def(MONITORING_JOB).execute_in_process(run_config=_run_config())

    assert result.success
    assert _metadata(result, "source_discovery")["running_monitoring_run_id"] == running.run_id
    assert _metadata(result, "monitoring_summary") == {"skipped": "source already being monitored"}
    assert upstream.discoveries == 0
    assert [run.id for run in service.repository.list_runs()] == [running.run_id]


def test_stage_failure_fails_the_monitoring_run_and_the_dagster_run(
    session_factory: sessionmaker[Session],
) -> None:
    upstream = FakeUpstream()
    upstream.discovery_error = ConnectionError("listing unreachable")
    service = build_service(session_factory, {"ovd-info": upstream})
    defs = build_definitions(service.settings, monitoring=service)

    result = defs.resolve_job_def(MONITORING_JOB).execute_in_process(
        run_config=_run_config(), raise_on_error=False
    )

    assert not result.success
    [run] = service.repository.list_runs()
    assert run.status is MonitoringRunStatus.FAILED
    assert run.error_message == "ConnectionError: listing unreachable"


class FlakyStore:
    """In-process Qdrant that is unavailable for the first `failures` collection checks."""

    def __init__(self, failures: int) -> None:
        self.failures = failures
        self._store = QdrantVectorStore(QdrantClient(":memory:"))

    def __getattr__(self, name: str) -> Any:
        return getattr(self._store, name)

    def ensure_collection(self, name: str, vector_size: int) -> None:
        if self.failures > 0:
            self.failures -= 1
            raise RetrievalUnavailableError("Qdrant unavailable during ensure_collection")
        self._store.ensure_collection(name, vector_size)


def test_derived_job_retries_retryable_semantic_failures(
    session_factory: sessionmaker[Session],
) -> None:
    upstream = FakeUpstream()
    upstream.publish("sidorov", SIDOROV)
    store = FlakyStore(failures=1)
    service = build_service(
        session_factory,
        {"ovd-info": upstream},
        create_semantic_indexer=lambda: semantic_indexer(session_factory, cast(VectorStore, store)),
    )
    service.run_source("ovd-info")  # its semantic stage consumes the single failure
    store.failures = 1
    defs = build_definitions(service.settings, monitoring=service, derived_retry_delay_seconds=0)

    result = defs.resolve_job_def(MONITORING_DERIVED_JOB).execute_in_process()

    assert result.success
    retries = [
        event for event in result.all_events if event.event_type_value == "STEP_UP_FOR_RETRY"
    ]
    assert len(retries) == 1
    derived = [
        run
        for run in reversed(service.repository.list_runs())
        if run.trigger_type is MonitoringTrigger.DERIVED
    ]
    assert [run.status for run in derived] == [
        MonitoringRunStatus.COMPLETED_WITH_ERRORS,
        MonitoringRunStatus.COMPLETED,
    ]
    assert derived[1].semantic_entities_indexed == 2
    assert upstream.discoveries == 1


def test_derived_job_does_not_retry_a_clean_run(session_factory: sessionmaker[Session]) -> None:
    service = build_service(session_factory, {"ovd-info": FakeUpstream()})
    defs = build_definitions(service.settings, monitoring=service, derived_retry_delay_seconds=0)

    result = defs.resolve_job_def(MONITORING_DERIVED_JOB).execute_in_process()

    assert result.success
    assert not [
        event for event in result.all_events if event.event_type_value == "STEP_UP_FOR_RETRY"
    ]
    assert result.output_for_node("run_derived_monitoring") == service.repository.list_runs()[0].id


def _retries(result: dg.ExecuteInProcessResult) -> int:
    return len(
        [event for event in result.all_events if event.event_type_value == "STEP_UP_FOR_RETRY"]
    )


def test_derived_job_does_not_retry_a_non_retryable_run_failure(
    session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    service = build_service(session_factory, {"ovd-info": FakeUpstream()})

    def broken(handle: object) -> None:
        raise ValueError("classifier misconfigured")

    monkeypatch.setattr(service, "classify", broken)
    defs = build_definitions(service.settings, monitoring=service, derived_retry_delay_seconds=0)

    result = defs.resolve_job_def(MONITORING_DERIVED_JOB).execute_in_process(raise_on_error=False)

    assert not result.success
    assert _retries(result) == 0
    [run] = service.repository.list_runs()
    assert run.status is MonitoringRunStatus.FAILED
    assert run.stage_metrics["run"]["failure_kind"] == "non_retryable"


def test_derived_job_retries_a_retryable_run_failure(
    session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    service = build_service(session_factory, {"ovd-info": FakeUpstream()})
    original = service.classify
    calls: list[int] = []

    def flaky(handle: RunHandle) -> StageResult:
        calls.append(1)
        if len(calls) == 1:
            raise OperationalError("SELECT 1", {}, Exception("server closed the connection"))
        return original(handle)

    monkeypatch.setattr(service, "classify", flaky)
    defs = build_definitions(service.settings, monitoring=service, derived_retry_delay_seconds=0)

    result = defs.resolve_job_def(MONITORING_DERIVED_JOB).execute_in_process()

    assert result.success
    assert _retries(result) == 1
    assert [run.status for run in reversed(service.repository.list_runs())] == [
        MonitoringRunStatus.FAILED,
        MonitoringRunStatus.COMPLETED,
    ]
