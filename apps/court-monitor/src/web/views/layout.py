"""The status strip's numbers and a safe external link: read without HTML."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from analysis.read import verdict_count
from ingestion.read import article_count
from orchestrator.read import latest_monitoring_status
from processing.read import group_count


@dataclass(frozen=True)
class StripNumbers:
    """The status strip's own numbers; `/api/v1/status` reads them too."""

    articles: int
    people: int
    result: int
    # The latest monitoring run's status code, or None before the first run.
    latest_monitoring_status: str | None


def strip_numbers(db: Session) -> StripNumbers:
    status = latest_monitoring_status(db)
    return StripNumbers(
        articles=article_count(db),
        people=group_count(db),
        result=verdict_count(db, verdict="political"),
        latest_monitoring_status=None if status is None else str(status),
    )


def external_url(url: str | None) -> str | None:
    """A scraped address fit for a link: http(s) only, never `javascript:` or the like."""
    if url and url.strip().lower().startswith(("http://", "https://")):
        return url.strip()
    return None
