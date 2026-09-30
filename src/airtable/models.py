"""What one sync of one table did, and what one sync of all four did."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

# The four tables, in the order the operator reads them.
TABLES = ("sources", "rfm_persons", "known_persons", "excluded_persons")

TABLE_LABELS = {
    "sources": "Источники",
    "rfm_persons": "Росфинмониторинг",
    "known_persons": "Найденные люди",
    "excluded_persons": "Исключения",
}


class TableStatus:
    SUCCESS = "success"
    ERROR = "error"


@dataclass
class TableSyncResult:
    """One table. `unchanged` is what makes a second run over an untouched Airtable a
    no-op: nothing created, nothing updated, every record reported as unchanged."""

    created: int = 0
    updated: int = 0
    unchanged: int = 0
    errors: int = 0
    # Airtable records read, however many of them were written.
    received: int = 0
    status: str = TableStatus.SUCCESS
    error: str | None = None

    @property
    def failed(self) -> bool:
        return self.status == TableStatus.ERROR


@dataclass
class SyncReport:
    """All four tables. `status` is `partial` when at least one table failed and at
    least one did not, so that one broken table never hides the other three."""

    started_at: datetime
    tables: dict[str, TableSyncResult] = field(default_factory=dict)
    finished_at: datetime | None = None

    @property
    def status(self) -> str:
        results = list(self.tables.values())
        if not results or all(result.failed for result in results):
            return "failed"
        if any(result.failed for result in results):
            return "partial"
        return "success"

    @property
    def duration_seconds(self) -> float:
        if self.finished_at is None:
            return 0.0
        return (self.finished_at - self.started_at).total_seconds()
