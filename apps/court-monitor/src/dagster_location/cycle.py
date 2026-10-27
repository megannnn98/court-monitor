"""The steps over the whole database that turn the night's load into the result.

The load (`monitoring_job`, one run per source) fills the articles; the people of the
result come from the operator's «Сделать всё» chain from its second step: purge, entities,
figurants, political. They delete and cost money, so the cycle runs as that very chain
through `OperationRegistry`: one live `monitor` operation at a time, across the console
and the schedule (the registry's unique index), the run visible in the console, and a step
that fails ends the chain.
"""

import re
import sys
import time
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import Final

import dagster as dg

from application import build_application_services
from orchestrator.dagster.jobs import MONITORING_JOB
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
    _run_process,
)

CYCLE_JOB = "monitoring_cycle_job"
CYCLE_SCHEDULE = "monitoring_cycle_schedule"
# The chain's steps after the load, in order: `monitor --mode <step>`.
CYCLE_STEPS: Final = ("purge", "entities", "figurants", "political")
# A step that outlives this is stopped (SIGINT, then kill) and ends the chain.
STEP_TIMEOUT = timedelta(hours=2)
# How long the cycle waits for the night's loads to end, and how recent a finished load
# must be for the cycle to have anything to work on.
LOAD_WAIT_LIMIT = timedelta(hours=2)
LOAD_LOOKBACK = timedelta(hours=12)
LOAD_POLL = timedelta(seconds=30)
_LOG_TAIL = 1500
# The CLI's summaries count what the model was not asked (budget spent, failures).
_UNASKED = re.compile(r'"\w*unasked"\s*:\s*(\d+)')
_FAILURES = re.compile(r'"\w*failures"\s*:\s*(\d+)')
# An exit code for a step the deadline stopped when the CLI answered the signal with 0.
_TIMEOUT_EXIT = 124
_ACTIVE_DAGSTER_RUNS = [
    dg.DagsterRunStatus.QUEUED,
    dg.DagsterRunStatus.NOT_STARTED,
    dg.DagsterRunStatus.STARTING,
    dg.DagsterRunStatus.STARTED,
]
# `main.py` runs from the repository, as the console's operations do.
COMMAND_LINE = CommandLine(
    program=(sys.executable, str(Path(__file__).resolve().parents[1] / "main.py")),
    cwd=Path(__file__).resolve().parents[4],
)


def _new_part(previous: str, current: str) -> str:
    """What `current` adds to `previous`; the registry hands over a sliding tail, not a log."""
    if not previous or current.startswith(previous):
        return current[len(previous) :]
    anchor = previous[-200:]
    at = current.find(anchor)
    return current[at + len(anchor) :] if at >= 0 else current


def bounded_process_runner(
    command_line: CommandLine, limit: timedelta, log: Callable[[str], None] = lambda _: None
) -> Callable[[list[str], Heartbeat], ProcessResult]:
    """The registry's process runner with a deadline and a live log.

    Past the deadline the heartbeat answers «stop», the process gets SIGINT, and the step
    ends failed whatever exit code the CLI gave on the signal. What the process writes goes
    to `log` as it comes, and a step's end is logged at once, not when the chain ends."""

    def run(command: list[str], heartbeat: Heartbeat) -> ProcessResult:
        step = " ".join(command[2:])
        deadline = time.monotonic() + limit.total_seconds()
        expired = False
        seen = {"out": "", "err": ""}

        def beat(stdout: str = "", stderr: str = "") -> bool:
            nonlocal expired
            for key, text_ in (("out", stdout), ("err", stderr)):
                fresh = _new_part(seen[key], text_)
                seen[key] = text_
                if fresh.strip():
                    log(f"[{step}] {fresh.rstrip()}")
            if time.monotonic() >= deadline:
                expired = True
            return heartbeat(stdout, stderr) and not expired

        result = _run_process(command, beat, cwd=command_line.cwd)
        if expired:
            result = ProcessResult(
                return_code=result.return_code or _TIMEOUT_EXIT,
                stdout=result.stdout,
                stderr=f"{result.stderr}\nstep exceeded its deadline of {limit}",
            )
        # The process ended after its last heartbeat: what it wrote since is still unlogged.
        for key, text_ in (("out", result.stdout), ("err", result.stderr)):
            fresh = _new_part(seen[key], text_)
            if fresh.strip():
                log(f"[{step}] {fresh.rstrip()}")
        log(f"[{step}] exit {result.return_code}")
        return result

    return run


def unasked_total(output: str) -> int:
    """What the steps' JSON summaries say they did not ask the model."""
    return sum(int(count) for count in _UNASKED.findall(output))


def failures_total(output: str) -> int:
    """What the steps' JSON summaries say the model failed on, with an exit code of 0."""
    return sum(int(count) for count in _FAILURES.findall(output))


def wait_for_load(
    *,
    load_active: Callable[[], bool],
    loaded_recently: Callable[[], bool],
    log: Callable[[str], None],
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> None:
    """Hold the cycle until the night's loads are over; refuse it when they gave nothing.

    The cycle deletes articles and spends money: never beside a load that still writes,
    never over a database no load has refreshed."""
    deadline = clock() + LOAD_WAIT_LIMIT.total_seconds()
    while load_active():
        if clock() >= deadline:
            raise dg.Failure(
                description=f"loads still running after {LOAD_WAIT_LIMIT}; the cycle did not start",
                allow_retries=False,
            )
        log("monitoring cycle: waiting for the loads to end")
        sleep(LOAD_POLL.total_seconds())
    if not loaded_recently():
        raise dg.Failure(
            description=f"no source finished a load in the last {LOAD_LOOKBACK}; the cycle skipped",
            allow_retries=False,
        )


def check_chain(runs: list[OperationRun]) -> None:
    """The chain must have run every step and every step must have succeeded."""
    modes = tuple(run.parameters.mode for run in runs)
    failed = next((run for run in runs if run.status is not OperationRunStatus.SUCCEEDED), None)
    if failed is not None:
        raise dg.Failure(
            description=(
                f"{failed.parameters.mode} ended {failed.status.value} "
                f"(exit {failed.return_code}): {failed.error or failed.stderr[-_LOG_TAIL:]}"
            ),
            allow_retries=False,
        )
    if modes != CYCLE_STEPS:
        raise dg.Failure(
            description=f"the chain ran {modes}, expected {CYCLE_STEPS}", allow_retries=False
        )


@dg.op
def run_monitoring_cycle(context: dg.OpExecutionContext) -> None:
    services = build_application_services()
    session_factory = services.session_factory
    repository = services.monitoring.repository

    def dagster_load_active() -> bool:
        return bool(
            context.instance.get_runs(
                dg.RunsFilter(job_name=MONITORING_JOB, statuses=_ACTIVE_DAGSTER_RUNS), limit=1
            )
        )

    wait_for_load(
        load_active=lambda: dagster_load_active() or repository.source_run_active(),
        loaded_recently=lambda: repository.source_run_finished_within(LOAD_LOOKBACK),
        log=context.log.info,
    )
    # The executor runs a step in this thread, and the next step of the chain after it:
    # `start` returns when the whole chain has ended.
    worker_id = f"dagster-cycle:{context.run_id}"
    registry = OperationRegistry(
        session_factory,
        command_line=COMMAND_LINE,
        executor=lambda work: work(),
        process_runner=bounded_process_runner(COMMAND_LINE, STEP_TIMEOUT, context.log.info),
        worker_id=worker_id,
    )
    try:
        first = registry.start("monitor", OperationParameters(mode="purge", chain=True))
    except OperationConflictError as exc:
        raise dg.Failure(
            description=f"another monitor operation is running; the cycle did not start: {exc}",
            allow_retries=False,
        ) from exc
    runs = registry.runs_of_worker("monitor", first.id)
    unasked = sum(unasked_total(run.stdout) for run in runs)
    failures = sum(failures_total(run.stdout) for run in runs)
    check_chain(runs)
    if unasked or failures:
        context.log.warning(
            f"monitoring cycle: {unasked} left unasked (budget), {failures} model failures; "
            "the next run takes them"
        )
    context.add_output_metadata(
        {
            "steps": ", ".join(run.parameters.mode or "" for run in runs),
            "unasked": unasked,
            "failures": failures,
        }
    )


@dg.job(name=CYCLE_JOB, executor_def=dg.in_process_executor)
def monitoring_cycle_job() -> None:
    run_monitoring_cycle()


def build_cycle_schedule(settings: MonitoringSettings) -> dg.ScheduleDefinition:
    return dg.ScheduleDefinition(
        name=CYCLE_SCHEDULE,
        cron_schedule=settings.cycle_cron,
        job=monitoring_cycle_job,
        default_status=dg.DefaultScheduleStatus.STOPPED,
        description="Purge, entities, figurants, political over the whole database",
    )
