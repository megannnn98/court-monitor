"""Monitoring run lifecycle, stale recovery, per-scope exclusion and checkpoints (PostgreSQL)."""

from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from monitoring.models import (
    FailureKind,
    MonitoringAlreadyRunningError,
    MonitoringRunAbortedError,
    MonitoringRunStatus,
    MonitoringStage,
    MonitoringTrigger,
    source_scope,
)
from monitoring.repository import SqlAlchemyMonitoringRepository
from sources.ingestion_errors import PermanentFetchError, TransientFetchError

STALE_AFTER = timedelta(minutes=120)


def _start(repository: SqlAlchemyMonitoringRepository, source: str = "ovd-info") -> int:
    return repository.start_run(
        scope=source_scope(source),
        source=source,
        trigger=MonitoringTrigger.MANUAL,
        parameters={"discovery_limit": 5},
        stale_after=STALE_AFTER,
    )


def test_run_completes_with_counters_and_stage_metrics(
    session_factory: sessionmaker[Session],
) -> None:
    repository = SqlAlchemyMonitoringRepository(session_factory)
    run_id = _start(repository)

    repository.add_counters(run_id, {"documents_discovered": 3, "documents_ingested": 2})
    repository.add_counters(run_id, {"documents_ingested": 1})
    repository.set_stage_metrics(run_id, MonitoringStage.DISCOVERY, {"references": 3})
    status = repository.finish_run(run_id)

    run = repository.get_run(run_id)
    assert status is MonitoringRunStatus.COMPLETED
    assert run is not None
    assert run.status is MonitoringRunStatus.COMPLETED
    assert (run.documents_discovered, run.documents_ingested) == (3, 3)
    assert run.stage_metrics == {"discovery": {"references": 3}}
    assert run.parameters == {"discovery_limit": 5}
    assert run.finished_at is not None
    assert run.duration_seconds is not None


def test_item_failures_complete_run_with_errors(session_factory: sessionmaker[Session]) -> None:
    repository = SqlAlchemyMonitoringRepository(session_factory)
    run_id = _start(repository)

    repository.record_failure(
        run_id,
        stage=MonitoringStage.INGESTION,
        entity_type="source_reference",
        external_ref="https://ovd.info/news/1",
        error=TransientFetchError("HTTP 503"),
    )
    repository.record_failure(
        run_id,
        stage=MonitoringStage.EXTRACTION,
        entity_type="article",
        entity_id=7,
        error=PermanentFetchError("gone"),
    )

    assert repository.finish_run(run_id) is MonitoringRunStatus.COMPLETED_WITH_ERRORS
    details = repository.get_run_details(run_id)
    assert details is not None
    assert details.run.error_count == 2
    assert [
        (item.stage, item.entity_id, item.external_ref, item.failure_kind, item.error_type)
        for item in details.items
    ] == [
        (
            MonitoringStage.INGESTION,
            None,
            "https://ovd.info/news/1",
            FailureKind.RETRYABLE,
            "TransientFetchError",
        ),
        (MonitoringStage.EXTRACTION, 7, None, FailureKind.NON_RETRYABLE, "PermanentFetchError"),
    ]


def test_failed_run_keeps_error_message(session_factory: sessionmaker[Session]) -> None:
    repository = SqlAlchemyMonitoringRepository(session_factory)
    run_id = _start(repository)

    status = repository.finish_run(run_id, error=RuntimeError("database went away"))

    run = repository.get_run(run_id)
    assert status is MonitoringRunStatus.FAILED
    assert run is not None
    assert run.error_message == "RuntimeError: database went away"


def test_same_scope_cannot_run_twice(session_factory: sessionmaker[Session]) -> None:
    repository = SqlAlchemyMonitoringRepository(session_factory)
    first = _start(repository)

    with pytest.raises(MonitoringAlreadyRunningError) as raised:
        _start(repository)

    assert raised.value.running_run_id == first
    repository.finish_run(first)
    assert _start(repository) != first


def test_different_sources_run_concurrently(session_factory: sessionmaker[Session]) -> None:
    repository = SqlAlchemyMonitoringRepository(session_factory)

    ovd = _start(repository, "ovd-info")
    sota = _start(repository, "sota-vision")

    assert ovd != sota
    assert {run.source for run in repository.list_runs(status=MonitoringRunStatus.RUNNING)} == {
        "ovd-info",
        "sota-vision",
    }


def test_simultaneous_same_source_starts_admit_exactly_one(
    session_factory: sessionmaker[Session],
) -> None:
    repository = SqlAlchemyMonitoringRepository(session_factory)
    barrier = threading.Barrier(4)
    started: list[int] = []
    rejected: list[MonitoringAlreadyRunningError] = []
    lock = threading.Lock()

    def start() -> None:
        barrier.wait()
        try:
            run_id = _start(repository)
        except MonitoringAlreadyRunningError as exc:
            with lock:
                rejected.append(exc)
        else:
            with lock:
                started.append(run_id)

    threads = [threading.Thread(target=start) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(started) == 1
    assert len(rejected) == 3


def test_stale_running_run_is_aborted_by_next_start(session_factory: sessionmaker[Session]) -> None:
    repository = SqlAlchemyMonitoringRepository(session_factory)
    stale = _start(repository)
    with session_factory.begin() as session:
        session.execute(
            text(
                "UPDATE monitoring_runs SET heartbeat_at = now() - interval '3 hours' WHERE id = :id"
            ),
            {"id": stale},
        )

    fresh = _start(repository)

    stale_run = repository.get_run(stale)
    assert stale_run is not None
    assert stale_run.status is MonitoringRunStatus.ABORTED
    assert stale_run.finished_at is not None
    assert stale_run.error_message is not None
    assert "stale" in stale_run.error_message
    # A late finish of the aborted run does not resurrect it.
    assert repository.finish_run(stale) is MonitoringRunStatus.ABORTED
    fresh_run = repository.get_run(fresh)
    assert fresh_run is not None
    assert fresh_run.status is MonitoringRunStatus.RUNNING


def test_recent_running_run_is_not_stale(session_factory: sessionmaker[Session]) -> None:
    repository = SqlAlchemyMonitoringRepository(session_factory)
    running = _start(repository)
    with session_factory.begin() as session:
        session.execute(
            text(
                "UPDATE monitoring_runs SET heartbeat_at = now() - interval '20 minutes' "
                "WHERE id = :id"
            ),
            {"id": running},
        )

    with pytest.raises(MonitoringAlreadyRunningError):
        _start(repository)


def test_checkpoint_update_is_per_source(session_factory: sessionmaker[Session]) -> None:
    repository = SqlAlchemyMonitoringRepository(session_factory)
    first = _start(repository)
    repository.update_checkpoint("ovd-info", run_id=first, discovered_count=4)
    repository.finish_run(first)
    second = _start(repository)
    repository.update_checkpoint("ovd-info", run_id=second, discovered_count=6)

    states = {state.source_name: state for state in repository.list_source_states()}

    assert set(states) == {"ovd-info"}
    assert states["ovd-info"].last_successful_run_id == second
    assert states["ovd-info"].last_discovered_count == 6
    assert states["ovd-info"].last_successful_run_at is not None
    assert states["ovd-info"].last_external_marker is None


def test_aborted_run_is_fenced_from_further_writes(session_factory: sessionmaker[Session]) -> None:
    repository = SqlAlchemyMonitoringRepository(session_factory)
    zombie = _start(repository)
    with session_factory.begin() as session:
        session.execute(
            text(
                "UPDATE monitoring_runs SET heartbeat_at = now() - interval '3 hours' WHERE id = :id"
            ),
            {"id": zombie},
        )
    replacement = _start(repository)

    writes: list[Callable[[], object]] = [
        lambda: repository.heartbeat(zombie),
        lambda: repository.add_counters(zombie, {"documents_ingested": 1}),
        lambda: repository.set_stage_metrics(zombie, MonitoringStage.INGESTION, {"x": 1}),
        lambda: repository.set_rf_snapshot(zombie, None),
        lambda: repository.record_failure(
            zombie,
            stage=MonitoringStage.INGESTION,
            entity_type="source_reference",
            error=TransientFetchError("HTTP 503"),
        ),
    ]
    for write in writes:
        with pytest.raises(MonitoringRunAbortedError):
            write()

    details = repository.get_run_details(zombie)
    assert details is not None
    assert details.run.status is MonitoringRunStatus.ABORTED
    assert (details.run.documents_ingested, details.run.error_count, details.items) == (0, 0, [])
    assert details.run.stage_metrics == {}
    repository.heartbeat(replacement)  # the live run is unaffected


def test_failed_run_records_its_failure_kind(session_factory: sessionmaker[Session]) -> None:
    repository = SqlAlchemyMonitoringRepository(session_factory)
    transient = _start(repository)
    repository.finish_run(transient, error=TransientFetchError("HTTP 503"))
    permanent = _start(repository)
    repository.finish_run(permanent, error=ValueError("bug"))

    runs = {run.id: run for run in repository.list_runs()}
    assert runs[transient].stage_metrics["run"] == {
        "failure_kind": "retryable",
        "error_type": "TransientFetchError",
    }
    assert runs[permanent].stage_metrics["run"]["failure_kind"] == "non_retryable"
