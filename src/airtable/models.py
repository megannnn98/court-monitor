"""What one sync of one table did, and what one sync of all four did."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

# The four tables, in the order the operator reads them.
TABLES = ("sources", "known_persons", "officials", "articles")

TABLE_LABELS = {
    "sources": "Источники",
    "known_persons": "Найденные люди",
    "officials": "Должностные лица",
    "articles": "Статьи",
}


# Where a list is read from. The order of preference is API, then share links, then
# exported files: the API carries each record's id, a share link needs nobody to export
# anything, and a file needs the operator every time.
MODE_API = "api"
MODE_SHARE = "share"
MODE_FILES = "files"


class TableStatus:
    SUCCESS = "success"
    ERROR = "error"
    # The operator exported no file for this list. Not a failure and not an empty list:
    # the table is left as it was, which is what an untouched list should do.
    SKIPPED = "skipped"


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
    # Rows of this list that the export no longer holds, removed — the list is a copy of
    # an Airtable view, and a view that lost a row means the copy has too.
    removed: int = 0
    # Why nothing was removed, when a list that is a copy was read and still left alone:
    # a download too short to be believed. Empty when the removal went ahead or was not
    # in question.
    removed_blocked: str | None = None
    status: str = TableStatus.SUCCESS
    error: str | None = None

    @property
    def failed(self) -> bool:
        return self.status == TableStatus.ERROR

    @property
    def skipped(self) -> bool:
        return self.status == TableStatus.SKIPPED


@dataclass
class SyncReport:
    """All four tables. `status` is `partial` when at least one table failed and at
    least one did not, so that one broken table never hides the other three.

    A skipped table (no exported file) is neither a success nor a failure: it counts
    towards neither, because leaving a list alone is the right outcome, not a problem.
    """

    started_at: datetime
    # Where the records came from: "api" or "files". The operator needs to know which
    # run happened, since the two modes are configured completely differently.
    mode: str = "api"
    tables: dict[str, TableSyncResult] = field(default_factory=dict)
    finished_at: datetime | None = None

    @property
    def status(self) -> str:
        results = [result for result in self.tables.values() if not result.skipped]
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
