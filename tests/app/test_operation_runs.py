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
from operator_console import (
    OUTPUT_LIMIT,
    Heartbeat,
    OperationConflictError,
    OperationNotFoundError,
    OperationParameters,
    OperationRegistry,
    OperationRunStatus,
    ProcessResult,
    ProcessRunner,
)

INGEST = OperationParameters(source="ovd-info", limit=3)


def _succeeding(command: list[str], heartbeat: Heartbeat) -> ProcessResult:
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
    process: ProcessRunner = _succeeding,
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

    def process(command: list[str], heartbeat: Heartbeat) -> ProcessResult:
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
    def failing(command: list[str], heartbeat: Heartbeat) -> ProcessResult:
        return ProcessResult(2, "", "boom")

    registry = _registry(session_factory, failing)
    run = registry.start("discover-and-ingest", INGEST)

    stored = registry.get(run.id)
    assert stored.status is OperationRunStatus.FAILED
    assert (stored.return_code, stored.stderr) == (2, "boom")


def test_an_exception_fails_the_run_and_frees_the_operation(
    session_factory: sessionmaker[Session],
) -> None:
    def raising(command: list[str], heartbeat: Heartbeat) -> ProcessResult:
        raise OSError("no such interpreter")

    registry = _registry(session_factory, raising)
    run = registry.start("discover-and-ingest", INGEST)

    stored = registry.get(run.id)
    assert stored.status is OperationRunStatus.FAILED
    assert stored.error == "OSError: no such interpreter"
    registry.start("discover-and-ingest", INGEST)  # not blocked by the failed run


def test_output_keeps_only_its_end(session_factory: sessionmaker[Session]) -> None:
    def verbose(command: list[str], heartbeat: Heartbeat) -> ProcessResult:
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

    def outlived(command: list[str], heartbeat: Heartbeat) -> ProcessResult:
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
    with pytest.raises(ValueError, match="not a news source"):
        registry.start("monitor", OperationParameters(sources=["memopzk-figurants"]))
    with pytest.raises(ValueError, match="only supported for monitor"):
        registry.start("classify-persecution", OperationParameters(sources=["ovd-info"]))

    assert registry.list_runs() == []


def test_monitor_command_preserves_explicit_news_source_selection(
    session_factory: sessionmaker[Session],
) -> None:
    commands: list[list[str]] = []

    def recording(command: list[str], heartbeat: Heartbeat) -> ProcessResult:
        commands.append(command)
        return ProcessResult(0, "", "")

    registry = _registry(session_factory, recording)
    run = registry.start(
        "monitor", OperationParameters(sources=["sota-vision", "ovd-info"], limit=7)
    )

    assert run.parameters.sources == ["sota-vision", "ovd-info"]
    assert commands[0][-7:] == [
        "--catch-up",
        "--selected-source",
        "sota-vision",
        "--selected-source",
        "ovd-info",
        "--limit",
        "7",
    ]


def test_the_command_is_an_argv_from_the_allowlist(session_factory: sessionmaker[Session]) -> None:
    commands: list[list[str]] = []

    def recording(command: list[str], heartbeat: Heartbeat) -> ProcessResult:
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

    def recording(command: list[str], heartbeat: Heartbeat) -> ProcessResult:
        commands.append(command)
        return ProcessResult(0, "", "")

    _registry(session_factory, recording).start("monitor", parameters)

    [command] = commands
    assert command[2:] == ["monitor", *arguments]


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


def test_another_api_worker_reads_the_stored_run(
    session_factory: sessionmaker[Session], test_engine: Engine
) -> None:
    """The JSON API outlives the operations page: a run started elsewhere (the bot's
    `/update`, the CLI) is served from the database by a worker that never saw it start."""
    executor, queued = _deferred()
    accepting = _registry(session_factory, executor=executor)
    run = accepting.start("discover-and-ingest", OperationParameters(source="ovd-info", limit=3))
    assert len(queued) == 1  # the work was handed off, not run in the call

    reading = OperationRegistry(create_session_factory(test_engine))
    with _client(session_factory, reading) as client:
        listed = client.get("/operations/runs").json()
        detail = client.get(f"/operations/runs/{run.id}").json()

    assert [item["id"] for item in listed] == [run.id]
    assert detail["status"] == "pending"


def test_the_real_process_runner_captures_output_and_beats(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sys

    import operator_console

    monkeypatch.setattr(operator_console, "HEARTBEAT_INTERVAL", timedelta(seconds=0.1))
    beats: list[int] = []
    command = [sys.executable, "-c", "import time; time.sleep(0.5); print('out')"]

    def beat(stdout: str = "", stderr: str = "") -> bool:
        beats.append(1)
        return True

    result = operator_console._run_process(command, beat)

    assert (result.return_code, result.stdout) == (0, "out\n")
    assert len(beats) >= 2


def test_a_failed_heartbeat_does_not_end_the_run(
    session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A database blip while beating must not mark the run failed while its process
    goes on: the operation would look free and could start a second time."""
    status_after_beat: list[OperationRunStatus] = []
    holder: list[OperationRegistry] = []

    def beating(command: list[str], heartbeat: Heartbeat) -> ProcessResult:
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

    def stop(stdout: str = "", stderr: str = "") -> bool:
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


def test_a_stopped_run_ends_its_process_and_keeps_the_output_so_far(
    session_factory: sessionmaker[Session], test_engine: Engine
) -> None:
    """The stop comes from another API process; the one running it learns at its beat."""
    other = OperationRegistry(create_session_factory(test_engine))
    seen: dict[str, object] = {}

    def stopped_midway(command: list[str], heartbeat: Heartbeat) -> ProcessResult:
        assert heartbeat("line 1\n", "INFO started\n") is True
        seen["live"] = other.get(other.list_runs()[0].id)
        other.stop(other.list_runs()[0].id)
        seen["keep_going"] = heartbeat("line 1\nline 2\n", "INFO started\n")
        return ProcessResult(-2, "line 1\nline 2\nlate\n", "INFO started\nKeyboardInterrupt\n")

    run = _registry(session_factory, stopped_midway).start("discover-and-ingest", INGEST)

    live = seen["live"]
    assert isinstance(live, type(run))
    assert (live.status, live.stdout, live.stderr) == (
        OperationRunStatus.RUNNING,
        "line 1\n",
        "INFO started\n",
    )
    assert seen["keep_going"] is False
    stored = other.get(run.id)
    assert stored.status is OperationRunStatus.INTERRUPTED
    assert stored.error == "stopped by the operator"
    assert stored.stderr == "INFO started\n"


def test_a_pending_run_stopped_before_it_starts_never_runs(
    session_factory: sessionmaker[Session],
) -> None:
    executor, queued = _deferred()
    commands: list[list[str]] = []

    def recording(command: list[str], heartbeat: Heartbeat) -> ProcessResult:
        commands.append(command)
        return ProcessResult(0, "", "")

    registry = _registry(session_factory, recording, executor=executor)
    run = registry.start("discover-and-ingest", INGEST)

    assert registry.stop(run.id) is True
    queued[0]()

    assert commands == []
    assert registry.get(run.id).status is OperationRunStatus.INTERRUPTED
    # The operation is free again.
    registry.start("discover-and-ingest", INGEST)


def test_stopping_an_ended_or_unknown_run(session_factory: sessionmaker[Session]) -> None:
    registry = _registry(session_factory)
    run = registry.start("discover-and-ingest", INGEST)

    assert registry.stop(run.id) is False
    assert registry.get(run.id).status is OperationRunStatus.SUCCEEDED
    with pytest.raises(OperationNotFoundError):
        registry.stop(run.id + 1000)


def test_the_real_process_runner_streams_output_and_interrupts_on_stop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sys

    import operator_console

    monkeypatch.setattr(operator_console, "HEARTBEAT_INTERVAL", timedelta(seconds=0.2))
    command = [
        sys.executable,
        "-c",
        (
            "import sys, time\n"
            "print('started')\n"
            "try:\n"
            "    time.sleep(60)\n"
            "except KeyboardInterrupt:\n"
            "    print('interrupted', file=sys.stderr)\n"
            "    sys.exit(130)\n"
        ),
    ]
    beats: list[str] = []

    def stop_once_started(stdout: str = "", stderr: str = "") -> bool:
        beats.append(stdout)
        return "started" not in stdout

    result = operator_console._run_process(command, stop_once_started)

    assert "started\n" in beats
    assert (result.return_code, result.stdout, result.stderr) == (
        130,
        "started\n",
        "interrupted\n",
    )
