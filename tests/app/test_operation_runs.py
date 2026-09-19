"""Operator operation runs in PostgreSQL: persistence, one live run per operation across
processes, status transitions, stale runs, output limits, and the API reading them."""

from __future__ import annotations

import os
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session, sessionmaker

from api import app, get_db, get_operation_registry
from db.database import create_session_factory
from monitoring.models import DERIVED_SCOPE, MonitoringTrigger, source_scope
from monitoring.repository import SqlAlchemyMonitoringRepository
from operator_console import (
    OUTPUT_LIMIT,
    OperationConflictError,
    OperationNotFoundError,
    OperationParameters,
    OperationRegistry,
    OperationRun,
    OperationRunStatus,
    ProcessResult,
)

INGEST = OperationParameters(source="ovd-info", limit=3)


def _succeeding(command: list[str], heartbeat: Callable[[], None]) -> ProcessResult:
    heartbeat()
    return ProcessResult(return_code=0, stdout="done\n", stderr="")


def _inline(work: Callable[[], None]) -> None:
    work()


def _deferred() -> tuple[Callable[[Callable[[], None]], None], list[Callable[[], None]]]:
    """An executor that keeps the work for the test to run: the run stays pending."""
    queued: list[Callable[[], None]] = []
    return queued.append, queued


def _registry(
    session_factory: sessionmaker[Session],
    process: Callable[[list[str], Callable[[], None]], ProcessResult] = _succeeding,
    **kwargs: object,
) -> OperationRegistry:
    return OperationRegistry(
        session_factory,
        executor=kwargs.pop("executor", _inline),  # type: ignore[arg-type]
        process_runner=process,
        **kwargs,  # type: ignore[arg-type]
    )


def test_a_run_is_stored_and_read_by_a_new_registry(
    session_factory: sessionmaker[Session], test_engine: Engine
) -> None:
    run = _registry(session_factory).start("discover-and-ingest", INGEST)

    # Another API process: its own session factory and registry.
    other = OperationRegistry(create_session_factory(test_engine))
    stored = other.get(run.id)

    assert stored.status is OperationRunStatus.SUCCEEDED
    assert stored.return_code == 0
    assert stored.stdout == "done\n"
    assert stored.parameters == OperationParameters(source="ovd-info", limit=3)
    assert stored.command[-5:] == ["discover-and-ingest", "--source", "ovd-info", "--limit", "3"]
    assert stored.started_at is not None and stored.finished_at is not None
    assert [listed.id for listed in other.list_runs()] == [run.id]


def test_the_same_operation_cannot_run_twice_across_processes(
    session_factory: sessionmaker[Session], test_engine: Engine
) -> None:
    executor, _queued = _deferred()
    first = _registry(session_factory, executor=executor)
    second = _registry(create_session_factory(test_engine), executor=executor)

    first.start("discover-and-ingest", INGEST)

    with pytest.raises(OperationConflictError):
        second.start("discover-and-ingest", INGEST)


def test_concurrent_starts_of_one_operation_admit_exactly_one(
    session_factory: sessionmaker[Session], test_engine: Engine
) -> None:
    executor, _queued = _deferred()
    registries = [
        _registry(create_session_factory(test_engine), executor=executor) for _ in range(6)
    ]
    barrier = threading.Barrier(len(registries))
    outcomes: list[str] = []

    def start(registry: OperationRegistry) -> None:
        barrier.wait()
        try:
            registry.start("classify-persecution", OperationParameters(limit=10))
            outcomes.append("started")
        except OperationConflictError:
            outcomes.append("conflict")

    threads = [threading.Thread(target=start, args=(registry,)) for registry in registries]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(outcomes) == ["conflict"] * 5 + ["started"]


def test_different_operations_run_side_by_side(session_factory: sessionmaker[Session]) -> None:
    executor, _queued = _deferred()
    registry = _registry(session_factory, executor=executor)

    registry.start("discover-and-ingest", INGEST)
    registry.start("classify-persecution", OperationParameters(limit=10))

    assert {run.status for run in registry.list_runs()} == {OperationRunStatus.PENDING}


def test_a_run_goes_pending_running_then_succeeded(session_factory: sessionmaker[Session]) -> None:
    executor, queued = _deferred()
    seen: list[OperationRunStatus] = []
    registry_holder: list[OperationRegistry] = []

    def process(command: list[str], heartbeat: Callable[[], None]) -> ProcessResult:
        seen.append(registry_holder[0].list_runs()[0].status)
        return ProcessResult(0, "ok", "")

    registry = _registry(session_factory, process, executor=executor)
    registry_holder.append(registry)
    run = registry.start("discover-and-ingest", INGEST)
    assert registry.get(run.id).status is OperationRunStatus.PENDING

    queued[0]()

    assert seen == [OperationRunStatus.RUNNING]
    assert registry.get(run.id).status is OperationRunStatus.SUCCEEDED


def test_a_nonzero_exit_fails_the_run(session_factory: sessionmaker[Session]) -> None:
    def failing(command: list[str], heartbeat: Callable[[], None]) -> ProcessResult:
        return ProcessResult(2, "", "boom")

    registry = _registry(session_factory, failing)
    run = registry.start("discover-and-ingest", INGEST)

    stored = registry.get(run.id)
    assert stored.status is OperationRunStatus.FAILED
    assert (stored.return_code, stored.stderr) == (2, "boom")


def test_an_exception_fails_the_run_and_frees_the_operation(
    session_factory: sessionmaker[Session],
) -> None:
    def raising(command: list[str], heartbeat: Callable[[], None]) -> ProcessResult:
        raise OSError("no such interpreter")

    registry = _registry(session_factory, raising)
    run = registry.start("discover-and-ingest", INGEST)

    stored = registry.get(run.id)
    assert stored.status is OperationRunStatus.FAILED
    assert stored.error == "OSError: no such interpreter"
    registry.start("discover-and-ingest", INGEST)  # not blocked by the failed run


def test_output_keeps_only_its_end(session_factory: sessionmaker[Session]) -> None:
    def verbose(command: list[str], heartbeat: Callable[[], None]) -> ProcessResult:
        return ProcessResult(0, "a" * OUTPUT_LIMIT + "END", "e" * (OUTPUT_LIMIT * 2))

    registry = _registry(session_factory, verbose)
    stored = registry.get(registry.start("discover-and-ingest", INGEST).id)

    assert len(stored.stdout) == OUTPUT_LIMIT and stored.stdout.endswith("END")
    assert len(stored.stderr) == OUTPUT_LIMIT


def test_a_run_without_heartbeat_is_interrupted_and_frees_the_operation(
    session_factory: sessionmaker[Session],
) -> None:
    executor, queued = _deferred()
    registry = _registry(session_factory, executor=executor, stale_after=timedelta(minutes=5))
    run = registry.start("discover-and-ingest", INGEST)
    queued.clear()
    with session_factory.begin() as session:
        session.execute(
            text(
                "UPDATE operator_operation_runs SET status = 'running', "
                "started_at = now() - interval '1 hour', heartbeat_at = now() - interval '1 hour', "
                "worker_id = 'dead-host:1' WHERE id = :id"
            ),
            {"id": run.id},
        )

    stored = registry.get(run.id)

    assert stored.status is OperationRunStatus.INTERRUPTED
    assert stored.error is not None and "no heartbeat" in stored.error
    registry.start("discover-and-ingest", INGEST)


def test_a_late_result_does_not_overwrite_an_interrupted_run(
    session_factory: sessionmaker[Session],
) -> None:
    holder: list[OperationRegistry] = []

    def outlived(command: list[str], heartbeat: Callable[[], None]) -> ProcessResult:
        # The run is declared dead while its process still works.
        with session_factory.begin() as session:
            session.execute(
                text("UPDATE operator_operation_runs SET heartbeat_at = now() - interval '1 hour'")
            )
        holder[0].interrupt_stale_runs()
        return ProcessResult(0, "finished too late", "")

    registry = _registry(session_factory, outlived, stale_after=timedelta(minutes=5))
    holder.append(registry)
    run = registry.start("discover-and-ingest", INGEST)

    stored = registry.get(run.id)
    assert stored.status is OperationRunStatus.INTERRUPTED
    assert stored.stdout == ""


def test_parameters_are_validated_before_anything_is_stored(
    session_factory: sessionmaker[Session],
) -> None:
    registry = _registry(session_factory)

    with pytest.raises(ValueError, match="unknown source"):
        registry.start("discover-and-ingest", OperationParameters(source="nowhere", limit=3))
    with pytest.raises(OperationNotFoundError):
        registry.start("drop-database", OperationParameters())
    with pytest.raises(ValueError):
        OperationParameters(limit=0)

    assert registry.list_runs() == []


def test_the_command_is_an_argv_from_the_allowlist(session_factory: sessionmaker[Session]) -> None:
    commands: list[list[str]] = []

    def recording(command: list[str], heartbeat: Callable[[], None]) -> ProcessResult:
        commands.append(command)
        return ProcessResult(0, "", "")

    registry = _registry(session_factory, recording)
    registry.start("resolve-people", OperationParameters(limit=7, workers=2))

    [command] = commands
    assert command[1].endswith("src/main.py")
    assert command[2:] == ["resolve-people", "--limit", "7", "--workers", "2"]


@pytest.mark.parametrize(
    ("parameters", "arguments"),
    [
        (OperationParameters(), ["--catch-up", "--limit", "50"]),
        (
            OperationParameters(source="ovd-info", limit=20),
            ["--catch-up", "--source", "ovd-info", "--limit", "20"],
        ),
    ],
)
def test_the_catch_up_runs_monitor_over_every_source_or_one(
    session_factory: sessionmaker[Session],
    parameters: OperationParameters,
    arguments: list[str],
) -> None:
    commands: list[list[str]] = []

    def recording(command: list[str], heartbeat: Callable[[], None]) -> ProcessResult:
        commands.append(command)
        return ProcessResult(0, "", "")

    _registry(session_factory, recording).start("monitor", parameters)

    [command] = commands
    assert command[2:] == ["monitor", *arguments]


def test_the_catch_up_form_offers_every_source(session_factory: sessionmaker[Session]) -> None:
    def override_get_db() -> Iterator[Session]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        page = TestClient(app).get("/ui/operations/monitor")
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert page.status_code == 200
    assert '<option value="" selected>Все источники</option>' in page.text
    assert 'value="50"' in page.text


@contextmanager
def _client(
    session_factory: sessionmaker[Session], registry: OperationRegistry
) -> Iterator[TestClient]:
    def override_get_db() -> Iterator[Session]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_operation_registry] = lambda: registry
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_operation_registry, None)


def test_confirm_returns_at_once_and_the_api_reads_the_stored_run(
    session_factory: sessionmaker[Session], test_engine: Engine
) -> None:
    executor, queued = _deferred()
    accepting = _registry(session_factory, executor=executor)

    with _client(session_factory, accepting) as client:
        confirmed = client.post(
            "/ui/operations/discover-and-ingest/confirm",
            params={"source": "ovd-info", "limit": 3},
            follow_redirects=False,
        )
        conflict = client.post(
            "/ui/operations/discover-and-ingest/confirm",
            params={"source": "ovd-info", "limit": 3},
            follow_redirects=False,
        )

    assert confirmed.status_code == 303
    assert conflict.status_code == 409
    assert len(queued) == 1  # the work was handed off, not run in the request
    run_id = int(confirmed.headers["location"].rsplit("/", 1)[1])

    # Another API worker, which never saw the run start, serves it from the database.
    reading = OperationRegistry(create_session_factory(test_engine))
    with _client(session_factory, reading) as client:
        listed = client.get("/operations/runs").json()
        detail = client.get(f"/operations/runs/{run_id}").json()
        page = client.get(f"/ui/operations/runs/{run_id}")

    assert [run["id"] for run in listed] == [run_id]
    assert detail["status"] == "pending"
    assert page.status_code == 200 and f"Run #{run_id}" in page.text


def test_the_real_process_runner_captures_output_and_beats(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sys

    import operator_console

    monkeypatch.setattr(operator_console, "HEARTBEAT_INTERVAL", timedelta(seconds=0.1))
    beats: list[int] = []
    command = [sys.executable, "-c", "import time; time.sleep(0.5); print('out')"]

    result = operator_console._run_process(command, lambda: beats.append(1))

    assert (result.return_code, result.stdout) == (0, "out\n")
    assert len(beats) >= 2


def test_a_failed_heartbeat_does_not_end_the_run(
    session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A database blip while beating must not mark the run failed while its process
    goes on: the operation would look free and could start a second time."""
    status_after_beat: list[OperationRunStatus] = []
    holder: list[OperationRegistry] = []

    def beating(command: list[str], heartbeat: Callable[[], None]) -> ProcessResult:
        heartbeat()
        status_after_beat.append(holder[0].list_runs()[0].status)
        return ProcessResult(0, "done\n", "")

    registry = _registry(session_factory, beating)
    holder.append(registry)
    original = OperationRegistry._running_here

    def broken_once(self: OperationRegistry, run_id: int) -> Any:
        monkeypatch.setattr(OperationRegistry, "_running_here", original)
        raise OperationalError("UPDATE", {}, Exception("server closed the connection"))

    monkeypatch.setattr(OperationRegistry, "_running_here", broken_once)

    run = registry.start("discover-and-ingest", INGEST)

    assert status_after_beat == [OperationRunStatus.RUNNING]
    assert registry.get(run.id).status is OperationRunStatus.SUCCEEDED


def test_an_exception_while_waiting_kills_the_process(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The run is recorded failed; its process must not live on unseen."""
    import sys

    import operator_console

    monkeypatch.setattr(operator_console, "HEARTBEAT_INTERVAL", timedelta(seconds=0.1))
    pid_file = tmp_path / "pid"
    command = [
        sys.executable,
        "-c",
        f"import os, time; open({str(pid_file)!r}, 'w').write(str(os.getpid())); time.sleep(60)",
    ]

    def stop(*_: object) -> None:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        operator_console._run_process(command, stop)

    pid = int(pid_file.read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_only_the_live_run_index_means_already_running(
    session_factory: sessionmaker[Session], test_engine: Engine
) -> None:
    """Any other constraint violation is a real error, not a conflict."""
    with test_engine.begin() as connection:
        connection.execute(
            text(
                "ALTER TABLE operator_operation_runs ADD CONSTRAINT ck_test_no_ingest "
                "CHECK (operation_name <> 'discover-and-ingest') NOT VALID"
            )
        )
    try:
        with pytest.raises(IntegrityError, match="ck_test_no_ingest"):
            _registry(session_factory).start("discover-and-ingest", INGEST)
    finally:
        with test_engine.begin() as connection:
            connection.execute(
                text("ALTER TABLE operator_operation_runs DROP CONSTRAINT ck_test_no_ingest")
            )


def _monitoring_run(
    repository: SqlAlchemyMonitoringRepository,
    source: str | None,
    *,
    finished: bool = True,
    error: BaseException | None = None,
    **counters: int,
) -> int:
    run_id = repository.start_run(
        scope=source_scope(source) if source else DERIVED_SCOPE,
        source=source,
        trigger=MonitoringTrigger.MANUAL,
        parameters={},
        stale_after=timedelta(hours=2),
    )
    if counters:
        repository.add_counters(run_id, counters)
    if finished:
        repository.finish_run(run_id, error=error)
    return run_id


def _running_catch_up(
    session_factory: sessionmaker[Session], registry: OperationRegistry
) -> OperationRun:
    run = registry.start("monitor", OperationParameters())
    with session_factory.begin() as session:
        session.execute(
            text(
                "UPDATE operator_operation_runs SET status = 'running', "
                "started_at = now() - interval '10 minutes', "
                "heartbeat_at = now() WHERE id = :id"
            ),
            {"id": run.id},
        )
    return registry.get(run.id)


def test_the_catch_up_page_shows_its_progress(
    session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MONITORING_ENABLED_SOURCES", "ovd-info,sota-vision,kommersant")
    executor, _queued = _deferred()
    registry = _registry(session_factory, executor=executor)
    monitoring = SqlAlchemyMonitoringRepository(session_factory)
    earlier = _monitoring_run(monitoring, "kommersant", documents_ingested=99)
    with session_factory.begin() as session:
        session.execute(
            text(
                "UPDATE monitoring_runs SET started_at = now() - interval '1 hour' WHERE id = :id"
            ),
            {"id": earlier},
        )
    run = _running_catch_up(session_factory, registry)
    _monitoring_run(monitoring, "ovd-info", documents_ingested=3, persons_created=2)
    _monitoring_run(monitoring, "sota-vision", finished=False, documents_ingested=1)

    with _client(session_factory, registry) as client:
        page = client.get(f"/ui/operations/runs/{run.id}").text

    assert '<progress value="1" max="4"></progress> 1 из 4 шагов' in page
    assert "Источники: 1 из 3 готово · сейчас: sota-vision" in page
    assert "Общая обработка: ожидает" in page
    assert "Загружено документов: 4" in page and "новых людей: 2" in page


def test_the_finished_catch_up_names_its_failed_sources(
    session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MONITORING_ENABLED_SOURCES", "ovd-info,sota-vision")
    executor, _queued = _deferred()
    registry = _registry(session_factory, executor=executor)
    monitoring = SqlAlchemyMonitoringRepository(session_factory)
    run = _running_catch_up(session_factory, registry)
    _monitoring_run(monitoring, "ovd-info", error=RuntimeError("listing is down"))
    _monitoring_run(monitoring, "sota-vision", documents_ingested=5)
    _monitoring_run(monitoring, None)
    with session_factory.begin() as session:
        session.execute(
            text(
                "UPDATE operator_operation_runs SET status = 'failed', finished_at = now() "
                "WHERE id = :id"
            ),
            {"id": run.id},
        )

    with _client(session_factory, registry) as client:
        page = client.get(f"/ui/operations/runs/{run.id}").text

    assert '<progress value="3" max="3"></progress> 3 из 3 шагов' in page
    assert "Общая обработка: готово" in page
    assert "источников с ошибкой: 1 (ovd-info)" in page


def test_other_operations_have_no_progress_bar(session_factory: sessionmaker[Session]) -> None:
    registry = _registry(session_factory)
    run = registry.start("classify-persecution", OperationParameters(limit=10))

    with _client(session_factory, registry) as client:
        page = client.get(f"/ui/operations/runs/{run.id}").text

    assert "<progress" not in page
