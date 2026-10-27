"""The steps over the whole database that turn the night's load into the result.

The load (`monitoring_job`, one run per source) fills the articles; the people of the
result come from these steps, the same ones «Сделать всё» chains in the operator console:
purge, entities, figurants, political. They delete and cost money, so a step that fails
stops the cycle.
"""

import subprocess
import sys
from pathlib import Path

import dagster as dg

from orchestrator.models import MonitoringSettings

CYCLE_JOB = "monitoring_cycle_job"
CYCLE_SCHEDULE = "monitoring_cycle_schedule"
CYCLE_COMMANDS = ("purge-junk", "collect-entities", "find-figurants", "find-political")
_MAIN = Path(__file__).resolve().parents[1] / "main.py"
_OUTPUT_TAIL = 2000


@dg.op
def run_monitoring_cycle(context: dg.OpExecutionContext) -> None:
    for command in CYCLE_COMMANDS:
        context.log.info(f"monitoring cycle: {command}")
        result = subprocess.run(
            [sys.executable, str(_MAIN), command], capture_output=True, text=True, check=False
        )
        context.log.info(f"{command}: exit {result.returncode}\n{result.stderr[-_OUTPUT_TAIL:]}")
        if result.returncode != 0:
            raise dg.Failure(
                description=f"{command} exited with {result.returncode}; the cycle stopped",
                allow_retries=False,
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
