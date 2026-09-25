"""`/update` and `/status` over the existing operator operation runs (ADR 0016).

The bot starts nothing of its own: `OperationRegistry.start("monitor", …)` records the
run, hands the `monitor --catch-up` process to an executor and returns at once, and its
partial unique index is what keeps a second update from starting. The numbers shown by
`/status` come from the monitoring runs that process leaves behind (ADR 0013).
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from monitoring.models import (
    FailureKind,
    MonitoringRunDetails,
    MonitoringRunStatus,
    MonitoringRunView,
    MonitoringTrigger,
)
from operator_console import (
    OperationConflictError,
    OperationParameters,
    OperationRun,
    OperationRunStatus,
)

logger = logging.getLogger(__name__)

MONITOR_OPERATION = "monitor"
# How many distinct failures of a run the status message names.
FAILURES_SHOWN = 3


class OperationStarter(Protocol):
    """The part of `OperationRegistry` the bot uses."""

    def start(self, name: str, parameters: OperationParameters) -> OperationRun: ...

    def runs_of(self, name: str, limit: int = ...) -> list[OperationRun]: ...


class MonitoringRunsReader(Protocol):
    """The part of `SqlAlchemyMonitoringRepository` the bot uses."""

    def list_runs_started_between(
        self, started: datetime, finished: datetime | None, *, trigger: MonitoringTrigger
    ) -> list[MonitoringRunView]: ...

    def get_run_details(self, run_id: int) -> MonitoringRunDetails | None: ...


@dataclass(frozen=True)
class UpdateAlreadyRunning:
    run: OperationRun


@dataclass(frozen=True)
class UpdateStarted:
    run: OperationRun
    sources: tuple[str, ...]


@dataclass(frozen=True)
class RunFailures:
    stage: str
    count: int
    kind: FailureKind
    message: str


@dataclass(frozen=True)
class UpdateStatus:
    """What `/status` shows: the operation run plus the monitoring work it produced."""

    run: OperationRun
    sources_done: int
    sources_total: int
    current_source: str | None
    derived_done: bool
    documents_discovered: int
    documents_ingested: int
    articles_extracted: int
    events_created: int
    persons_created: int
    persons_linked: int
    reviews_created: int
    error_count: int
    failures: tuple[RunFailures, ...]


class UpdateService:
    def __init__(
        self,
        operations: OperationStarter,
        monitoring_runs: MonitoringRunsReader,
        *,
        enabled_sources: Sequence[str],
        discovery_limit: int,
    ) -> None:
        self._operations = operations
        self._monitoring_runs = monitoring_runs
        self._enabled_sources = tuple(enabled_sources)
        self._discovery_limit = discovery_limit

    def start_update(self) -> UpdateStarted | UpdateAlreadyRunning:
        """Returns as soon as the run is recorded: the pipeline goes on in its own process."""
        try:
            run = self._operations.start(
                MONITOR_OPERATION, OperationParameters(limit=self._discovery_limit)
            )
        except OperationConflictError:
            active = self._active_run()
            if active is None:
                # It finished between the conflict and this read: the caller may retry.
                raise
            logger.info(
                "event=telegram_command command=update result=already_running run_id=%s", active.id
            )
            return UpdateAlreadyRunning(run=active)
        logger.info(
            "event=telegram_command command=update result=accepted run_id=%s sources=%d",
            run.id,
            len(self._enabled_sources),
        )
        return UpdateStarted(run=run, sources=self._enabled_sources)

    def last_update(self) -> UpdateStatus | None:
        # A purge, an entity rebuild or check is a monitor run too, but no update of the
        # sources.
        runs = [
            run
            for run in self._operations.runs_of(MONITOR_OPERATION, limit=5)
            if run.parameters.mode not in ("purge", "entities", "rosfin")
        ]
        return None if not runs else self._status_of(runs[0])

    def _active_run(self) -> OperationRun | None:
        live = {OperationRunStatus.PENDING, OperationRunStatus.RUNNING}
        return next(
            (
                run
                for run in self._operations.runs_of(MONITOR_OPERATION, limit=5)
                if run.status in live
            ),
            None,
        )

    def _status_of(self, run: OperationRun) -> UpdateStatus:
        sources = (
            tuple(run.parameters.sources)
            if run.parameters.sources is not None
            else (run.parameters.source,)
            if run.parameters.source is not None
            else self._enabled_sources
        )
        monitoring = (
            []
            if run.started_at is None
            else self._monitoring_runs.list_runs_started_between(
                run.started_at, run.finished_at, trigger=MonitoringTrigger.MANUAL
            )
        )
        per_source = [item for item in monitoring if item.source is not None]
        derived = next((item for item in monitoring if item.source is None), None)
        finished = [item for item in per_source if item.status is not MonitoringRunStatus.RUNNING]
        return UpdateStatus(
            run=run,
            sources_done=len(finished),
            sources_total=len(sources),
            current_source=next(
                (
                    item.source
                    for item in per_source
                    if item.status is MonitoringRunStatus.RUNNING and item.source is not None
                ),
                None,
            ),
            derived_done=derived is not None and derived.status is not MonitoringRunStatus.RUNNING,
            documents_discovered=sum(item.documents_discovered for item in per_source),
            documents_ingested=sum(item.documents_ingested for item in per_source),
            articles_extracted=sum(item.articles_extracted for item in monitoring),
            events_created=sum(item.events_created for item in monitoring),
            persons_created=sum(item.persons_created for item in monitoring),
            persons_linked=sum(item.persons_linked for item in monitoring),
            reviews_created=sum(item.person_reviews_created for item in monitoring),
            error_count=sum(item.error_count for item in monitoring),
            failures=self._failures(monitoring),
        )

    def _failures(self, monitoring: Sequence[MonitoringRunView]) -> tuple[RunFailures, ...]:
        """Grouped by stage and kind; the message is the error type, never a traceback."""
        grouped: dict[tuple[str, FailureKind, str], int] = {}
        for run in monitoring:
            if run.error_count == 0:
                continue
            details = self._monitoring_runs.get_run_details(run.id)
            if details is None:
                continue
            for item in details.items:
                key = (item.stage.value, item.failure_kind, item.error_type)
                grouped[key] = grouped.get(key, 0) + 1
        ordered = sorted(grouped.items(), key=lambda entry: (-entry[1], entry[0]))
        return tuple(
            RunFailures(stage=stage, count=count, kind=kind, message=error_type)
            for (stage, kind, error_type), count in ordered[:FAILURES_SHOWN]
        )
