"""What a reader may ask of the monitoring runs: status, runs, one run with its failed
items. Read only; a run is started and moved by `orchestrator.service`."""

from __future__ import annotations

from datetime import datetime

from orchestrator.models import (
    MonitoringRunDetails,
    MonitoringRunStatus,
    MonitoringRunView,
    MonitoringStatusView,
    MonitoringTrigger,
)
from orchestrator.repository import SqlAlchemyMonitoringRepository


class MonitoringRuns:
    def __init__(self, repository: SqlAlchemyMonitoringRepository) -> None:
        self._repository = repository

    def status(self) -> MonitoringStatusView:
        """Running and latest runs, source checkpoints, active findings."""
        return self._repository.status()

    def list_runs(
        self,
        *,
        limit: int = 20,
        offset: int = 0,
        source: str | None = None,
        status: MonitoringRunStatus | None = None,
    ) -> list[MonitoringRunView]:
        return self._repository.list_runs(limit=limit, offset=offset, source=source, status=status)

    def get_run_details(self, run_id: int) -> MonitoringRunDetails | None:
        return self._repository.get_run_details(run_id)

    def list_runs_started_between(
        self, started: datetime, finished: datetime | None, *, trigger: MonitoringTrigger
    ) -> list[MonitoringRunView]:
        """Runs of one trigger started within [started, finished], oldest first."""
        return self._repository.list_runs_started_between(started, finished, trigger=trigger)
