"""Sources whose site may have changed its layout, told from their own runs.

A site that changes its markup does not fail loudly: on 16.10.2026 every Kommersant page
failed with «Article text not found» among the ordinary errors of the console, and a
listing that no longer parses would find nothing at all — which looks like a quiet day.
Two signals, read from `monitoring_runs` and `monitoring_run_items` of the last
`HISTORY_DAYS` (both), asking no site:

- `parse` — the articles do not parse: the source's latest run of the window has at least
  `PARSE_FAILURES_MIN` documents failed with `ParseError` (a parser's own failure, not a
  network one; `NoTextError`, a post without text, is not counted), and they are at least
  `PARSE_FAILURE_SHARE` of what it fetched.
- `silent` — the listing finds nothing: the last `SILENT_RUNS` finished runs found no
  document at all, for at least `SILENT_MIN_HOURS`, though the source found some in the
  last `HISTORY_DAYS`. «Found» counts what is already stored too (a channel's page of the
  last 30 days, a site's first page), so a working source of any pace finds something;
  a channel quiet for more than its window is the one honest false alarm, and it ends
  once its last find is older than `HISTORY_DAYS`.

A source not run for `HISTORY_DAYS` has no run to tell by and is not reported, failing or
not. The window bounds the scan (an index on a source's latest runs, newest first); it is
not one lookup per source.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from sqlalchemy import text
from sqlalchemy.orm import Session

PARSE_FAILURES_MIN = 3
PARSE_FAILURE_SHARE = 0.5
SILENT_RUNS = 3
SILENT_MIN_HOURS = 24
HISTORY_DAYS = 30

_FINISHED = ("completed", "completed_with_errors")

# The latest finished run of each source (or of one), with its parse failures.
_LATEST_RUNS = text(
    """
    SELECT DISTINCT ON (r.source)
        r.id, r.source, r.started_at, r.documents_ingested, r.documents_failed,
        (SELECT count(*) FROM monitoring_run_items i
          WHERE i.run_id = r.id AND i.status = 'failed' AND i.error_type = 'ParseError')
          AS parse_failed,
        (SELECT left(i.error_message, 200) || ' — ' || coalesce(i.external_ref, '')
           FROM monitoring_run_items i
          WHERE i.run_id = r.id AND i.status = 'failed' AND i.error_type = 'ParseError'
          ORDER BY i.id LIMIT 1) AS example
    FROM monitoring_runs r
    WHERE r.source IS NOT NULL AND r.finished_at IS NOT NULL AND r.status = ANY(:finished)
      AND r.started_at >= :since
      AND (CAST(:source AS text) IS NULL OR r.source = :source)
    ORDER BY r.source, r.started_at DESC, r.id DESC
    """
)
# Every finished run of the window, newest first per source.
_RECENT_RUNS = text(
    """
    SELECT r.id, r.source, r.started_at, r.documents_discovered
    FROM monitoring_runs r
    WHERE r.source IS NOT NULL AND r.finished_at IS NOT NULL AND r.status = ANY(:finished)
      AND r.started_at >= :since
      AND (CAST(:source AS text) IS NULL OR r.source = :source)
    ORDER BY r.source, r.started_at DESC, r.id DESC
    """
)


@dataclass(frozen=True)
class LayoutSuspect:
    source: str
    signal: Literal["parse", "silent"]
    # The run that tells it: the latest one (`parse`), the first of the silent ones (`silent`).
    run_id: int
    since: datetime
    # `parse`: documents failed to parse of those fetched; `silent`: runs that found nothing.
    count: int
    total: int
    example: str | None = None

    @property
    def text(self) -> str:
        if self.signal == "parse":
            return (
                f"{self.source}: в запуске №{self.run_id} не разобраны {self.count} из "
                f"{self.total} статей — похоже, сайт сменил вёрстку статьи."
            )
        return (
            f"{self.source}: {self.count} запуска подряд с {self.since:%d.%m %H:%M} ничего не "
            "нашли, хотя раньше находили — сменилась вёрстка списка или источник замолчал."
        )


def layout_suspects(
    session: Session, *, source: str | None = None, now: datetime | None = None
) -> list[LayoutSuspect]:
    """The sources (or the one) that look broken by a change of layout, by source."""
    now = now or datetime.now(UTC)
    found: list[LayoutSuspect] = []
    since = now - timedelta(days=HISTORY_DAYS)
    # The latest run of the window: a source not run for a month has no run to tell by.
    for row in session.execute(
        _LATEST_RUNS, {"finished": list(_FINISHED), "source": source, "since": since}
    ):
        fetched = row.documents_ingested + row.documents_failed
        if (
            row.parse_failed >= PARSE_FAILURES_MIN
            and row.parse_failed >= PARSE_FAILURE_SHARE * fetched
        ):
            found.append(
                LayoutSuspect(
                    source=row.source,
                    signal="parse",
                    run_id=row.id,
                    since=row.started_at,
                    count=row.parse_failed,
                    total=fetched,
                    example=row.example,
                )
            )
    runs: dict[str, list[tuple[int, datetime, int]]] = {}
    for row in session.execute(
        _RECENT_RUNS,
        {
            "finished": list(_FINISHED),
            "since": since,
            "source": source,
        },
    ):
        runs.setdefault(row.source, []).append((row.id, row.started_at, row.documents_discovered))
    for name, history in runs.items():
        streak = []
        for run in history:
            if run[2]:
                break
            streak.append(run)
        found_before = len(streak) < len(history)
        if not found_before or len(streak) < SILENT_RUNS:
            continue
        first_id, first_at, _ = streak[-1]
        if now - first_at < timedelta(hours=SILENT_MIN_HOURS):
            continue
        found.append(
            LayoutSuspect(
                source=name,
                signal="silent",
                run_id=first_id,
                since=first_at,
                count=len(streak),
                total=len(history),
            )
        )
    return sorted(found, key=lambda suspect: (suspect.source, suspect.signal))
