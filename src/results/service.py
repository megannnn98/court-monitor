"""The results module's public API: what a reader is shown.

Findings are recorded from the verdicts of analysis and listed here; nothing in this
module decides a verdict.
"""

from __future__ import annotations

from results.findings import NO_RF_SNAPSHOT, FindingEvaluation, MonitoringFindingService
from results.models import MonitoringFindingView

__all__ = ["NO_RF_SNAPSHOT", "FindingEvaluation", "ResultsService"]


class ResultsService:
    def __init__(self, *, findings: MonitoringFindingService) -> None:
        self._findings = findings

    def evaluate_findings(self, *, run_id: int, snapshot_id: int | None) -> FindingEvaluation:
        """Record which persons satisfy each finding criterion now."""
        return self._findings.evaluate(run_id=run_id, snapshot_id=snapshot_id)

    def list_findings(
        self, *, active_only: bool = True, limit: int = 100, offset: int = 0
    ) -> list[MonitoringFindingView]:
        return self._findings.list_findings(active_only=active_only, limit=limit, offset=offset)
