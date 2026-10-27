"""The daily cycle (purge, entities, figurants, political): the chain it runs, the loads it
waits for, the deadline of a step and what it reports. Processes are stubs: nothing here
asks a model or deletes an article."""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import dagster as dg
import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from dagster_location import cycle
from dagster_location.cycle import (
    CYCLE_SCHEDULE,
    CYCLE_STEPS,
    LOAD_WAIT_LIMIT,
    bounded_process_runner,
    build_cycle_schedule,
    check_chain,
    failures_total,
    unasked_total,
    wait_for_load,
)
from orchestrator.models import MonitoringSettings
from orchestrator.operations import (
    CommandLine,
    Heartbeat,
    OperationConflictError,
    OperationParameters,
    OperationRegistry,
    OperationRun,
    OperationRunStatus,
    ProcessResult,
)
from orchestrator.repository import SqlAlchemyMonitoringRepository
from orchestrator.storage.models import MonitoringRunRecord


def _run(mode: str, status: OperationRunStatus = OperationRunStatus.SUCCEEDED) -> OperationRun:
    return OperationRun(
        id=1,
        operation=None,  # type: ignore[arg-type]
        parameters=OperationParameters(mode=mode),  # type: ignore[arg-type]
        status=status,
        created_at=None,  # type: ignore[arg-type]
        return_code=0 if status is OperationRunStatus.SUCCEEDED else 1,
        stderr="boom",
    )


def test_the_schedule_is_stopped_and_follows_the_cycle_cron() -> None:
    schedule = build_cycle_schedule(MonitoringSettings(cycle_cron="10 6 * * *"))

    assert schedule.name == CYCLE_SCHEDULE
    assert schedule.cron_schedule == "10 6 * * *"
    assert schedule.default_status is dg.DefaultScheduleStatus.STOPPED


def test_the_cycle_cron_needs_five_fields() -> None:
    with pytest.raises(ValueError, match="MONITORING_CYCLE_CRON"):
        MonitoringSettings(cycle_cron="5 *")


def test_unasked_counts_every_summary() -> None:
    output = '{"normalize_unasked": 2, "unasked": 3}\n{"sentences_unasked": 5, "failures": 9}'

    assert unasked_total(output) == 10
    assert unasked_total("done") == 0


def test_a_full_chain_passes_and_a_failed_or_short_one_does_not() -> None:
    check_chain([_run(mode) for mode in CYCLE_STEPS])

    with pytest.raises(dg.Failure, match="entities ended failed"):
        check_chain([_run("purge"), _run("entities", OperationRunStatus.FAILED)])
    with pytest.raises(dg.Failure, match="expected"):
        check_chain([_run("purge"), _run("entities")])


def test_it_waits_while_a_load_runs_and_then_goes() -> None:
    active = iter([True, True, False])
    waited: list[float] = []

    wait_for_load(
        load_active=lambda: next(active),
        loaded_recently=lambda: True,
        log=lambda _message: None,
        sleep=waited.append,
    )

    assert len(waited) == 2


def test_it_gives_up_when_the_loads_never_end() -> None:
    now = [0.0]

    def sleep(seconds: float) -> None:
        now[0] += seconds

    with pytest.raises(dg.Failure, match="still running"):
        wait_for_load(
            load_active=lambda: True,
            loaded_recently=lambda: True,
            log=lambda _message: None,
            sleep=sleep,
            clock=lambda: now[0],
        )

    assert now[0] >= LOAD_WAIT_LIMIT.total_seconds()


def test_it_refuses_to_run_when_no_load_finished() -> None:
    with pytest.raises(dg.Failure, match="no source finished"):
        wait_for_load(
            load_active=lambda: False,
            loaded_recently=lambda: False,
            log=lambda _message: None,
        )


def test_a_step_past_its_deadline_is_told_to_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = iter([0.0, 1.0, 11.0])
    answers: list[bool] = []

    def process(
        command: list[str], heartbeat: Heartbeat, *, cwd: Path | None = None
    ) -> ProcessResult:
        answers.append(heartbeat("", ""))
        answers.append(heartbeat("", ""))
        return ProcessResult(return_code=-2, stdout="", stderr="")

    monkeypatch.setattr(cycle, "_run_process", process)
    monkeypatch.setattr(time, "monotonic", lambda: next(clock))
    runner = bounded_process_runner(
        CommandLine(program=("x",), cwd=Path(".")), timedelta(seconds=10)
    )

    runner(["x"], lambda _stdout="", _stderr="": True)

    assert answers == [True, False]


WORKER = "dagster-cycle:test"


def _registry(
    session_factory: sessionmaker[Session], process: Callable[[list[str], Heartbeat], ProcessResult]
) -> OperationRegistry:
    return OperationRegistry(
        session_factory,
        command_line=CommandLine(program=("main.py",), cwd=Path(".")),
        executor=lambda work: work(),
        process_runner=process,
        worker_id=WORKER,
    )


def _ok(_command: list[str], heartbeat: Heartbeat) -> ProcessResult:
    heartbeat()
    return ProcessResult(return_code=0, stdout='{"unasked": 1}', stderr="")


def _chain_rows(session_factory: sessionmaker[Session]) -> list[tuple[str | None, str]]:
    with session_factory() as session:
        rows = session.execute(
            text(
                "SELECT parameters->>'mode', status FROM operator_operation_runs "
                "WHERE operation_name = 'monitor' ORDER BY id"
            )
        ).all()
    return [(mode, status) for mode, status in rows]


def test_the_chain_from_purge_runs_the_four_steps_to_the_last(
    session_factory: sessionmaker[Session],
) -> None:
    registry = _registry(session_factory, _ok)

    first = registry.start("monitor", OperationParameters(mode="purge", chain=True))

    assert [mode for mode, _status in _chain_rows(session_factory)] == list(CYCLE_STEPS)
    assert {status for _mode, status in _chain_rows(session_factory)} == {"succeeded"}
    runs = registry.runs_of_worker("monitor", first.id)
    check_chain(runs)
    assert sum(unasked_total(run.stdout) for run in runs) == 4


def test_a_failing_step_ends_the_chain_and_the_report(
    session_factory: sessionmaker[Session],
) -> None:
    def failing_on_figurants(command: list[str], heartbeat: Heartbeat) -> ProcessResult:
        heartbeat()
        failed = "find-figurants" in command
        return ProcessResult(
            return_code=1 if failed else 0, stdout="", stderr="no key" if failed else ""
        )

    registry = _registry(session_factory, failing_on_figurants)

    first = registry.start("monitor", OperationParameters(mode="purge", chain=True))

    assert [mode for mode, _status in _chain_rows(session_factory)] == [
        "purge",
        "entities",
        "figurants",
    ]
    with pytest.raises(dg.Failure, match="figurants ended failed"):
        check_chain(registry.runs_of_worker("monitor", first.id))


def test_a_live_operation_keeps_the_cycle_out(session_factory: sessionmaker[Session]) -> None:
    started = _registry(session_factory, _ok)
    # The console's run is live: the executor never ends it.
    live = OperationRegistry(
        session_factory,
        command_line=CommandLine(program=("main.py",), cwd=Path(".")),
        executor=lambda _work: None,
    )
    live.start("monitor", OperationParameters(mode="figurants"))

    with pytest.raises(OperationConflictError):
        started.start("monitor", OperationParameters(mode="purge", chain=True))


def _source_run(
    session_factory: sessionmaker[Session], *, source: str | None, status: str, ended_ago: timedelta
) -> None:
    now = datetime.now(UTC)
    with session_factory.begin() as session:
        session.add(
            MonitoringRunRecord(
                scope="all" if source is None else f"source:{source}",
                source=source,
                trigger_type="manual",
                status=status,
                parameters={},
                started_at=now - ended_ago - timedelta(minutes=5),
                heartbeat_at=now,
                finished_at=None if status == "running" else now - ended_ago,
            )
        )


def test_only_a_running_source_run_counts_as_a_load(session_factory: sessionmaker[Session]) -> None:
    repository = SqlAlchemyMonitoringRepository(session_factory)
    assert repository.source_run_active() is False

    _source_run(session_factory, source=None, status="running", ended_ago=timedelta())
    _source_run(session_factory, source="ovd-info", status="completed", ended_ago=timedelta())
    assert repository.source_run_active() is False

    _source_run(session_factory, source="ovd-info", status="running", ended_ago=timedelta())
    assert repository.source_run_active() is True


def test_a_load_counts_as_recent_by_the_time_it_ended(
    session_factory: sessionmaker[Session],
) -> None:
    repository = SqlAlchemyMonitoringRepository(session_factory)
    window = timedelta(hours=12)
    assert repository.source_run_finished_within(window) is False

    _source_run(session_factory, source="ovd-info", status="failed", ended_ago=timedelta(hours=1))
    _source_run(
        session_factory, source="sota-vision", status="completed", ended_ago=timedelta(hours=13)
    )
    assert repository.source_run_finished_within(window) is False

    _source_run(
        session_factory,
        source="tg-mash",
        status="completed_with_errors",
        ended_ago=timedelta(hours=2),
    )
    assert repository.source_run_finished_within(window) is True


def test_a_step_stopped_by_the_deadline_fails_even_when_the_cli_answers_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = iter([0.0, 11.0])

    def process(
        command: list[str], heartbeat: Heartbeat, *, cwd: Path | None = None
    ) -> ProcessResult:
        heartbeat("", "")
        return ProcessResult(return_code=0, stdout="", stderr="")

    monkeypatch.setattr(cycle, "_run_process", process)
    monkeypatch.setattr(time, "monotonic", lambda: next(clock))
    runner = bounded_process_runner(
        CommandLine(program=("x",), cwd=Path(".")), timedelta(seconds=10)
    )

    result = runner(["x"], lambda _stdout="", _stderr="": True)

    assert result.return_code != 0
    assert "deadline" in result.stderr


def test_what_a_step_writes_reaches_the_log_as_it_comes(monkeypatch: pytest.MonkeyPatch) -> None:
    logged: list[str] = []

    def process(
        command: list[str], heartbeat: Heartbeat, *, cwd: Path | None = None
    ) -> ProcessResult:
        heartbeat("", "model refused\n")
        heartbeat("", "model refused\nlater line\n")
        return ProcessResult(return_code=0, stdout='{"unasked": 0}', stderr="")

    monkeypatch.setattr(cycle, "_run_process", process)
    runner = bounded_process_runner(
        CommandLine(program=("x",), cwd=Path(".")), timedelta(hours=1), logged.append
    )

    runner(["python", "main.py", "monitor"], lambda _stdout="", _stderr="": True)

    assert logged[:2] == ["[monitor] model refused", "[monitor] later line"]
    assert logged[-1] == "[monitor] exit 0"


def test_model_failures_are_counted_apart_from_unasked() -> None:
    assert failures_total('{"failures": 61, "unasked": 0}') == 61


def test_a_console_run_after_the_chain_is_not_the_cycles(
    session_factory: sessionmaker[Session],
) -> None:
    registry = _registry(session_factory, _ok)
    first = registry.start("monitor", OperationParameters(mode="purge", chain=True))
    console = OperationRegistry(
        session_factory,
        command_line=CommandLine(program=("main.py",), cwd=Path(".")),
        executor=lambda work: work(),
        process_runner=_ok,
        worker_id="api:1",
    )
    console.start("monitor", OperationParameters(mode="figurants"))

    runs = registry.runs_of_worker("monitor", first.id)

    assert tuple(run.parameters.mode for run in runs) == CYCLE_STEPS


def test_what_a_short_step_wrote_without_a_heartbeat_is_logged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logged: list[str] = []

    def quick(
        command: list[str], heartbeat: Heartbeat, *, cwd: Path | None = None
    ) -> ProcessResult:
        return ProcessResult(return_code=0, stdout="summary", stderr="WARNING budget")

    def after_a_beat(
        command: list[str], heartbeat: Heartbeat, *, cwd: Path | None = None
    ) -> ProcessResult:
        heartbeat("", "first")
        return ProcessResult(return_code=0, stdout="", stderr="first\nlast warning")

    runner_line = CommandLine(program=("x",), cwd=Path("."))
    for process in (quick, after_a_beat):
        logged.clear()
        monkeypatch.setattr(cycle, "_run_process", process)
        bounded_process_runner(runner_line, timedelta(hours=1), logged.append)(
            ["python", "main.py", "monitor"], lambda _stdout="", _stderr="": True
        )
        text = "\n".join(logged)
        assert "WARNING budget" in text or "last warning" in text
