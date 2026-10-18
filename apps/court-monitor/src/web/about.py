"""What «О системе» reads: the build stamp, the totals behind the status strip, and the
last successful run. Shared by the legacy page and `GET /api/v1/about`, so both say the
same thing. Read-only."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from ingestion.read import article_count
from orchestrator.read import last_successful_operation_at
from processing.read import group_count
from web.build_info import build_info
from web.response_models import AboutResponse

__all__ = ["read_about"]


def read_about(db: Session) -> AboutResponse:
    """The build this instance runs and the data it is looking at, as of now."""
    info = build_info()
    return AboutResponse(
        version=info.version,
        tag=info.tag,
        commit=info.commit,
        built_at=info.built_at,
        articles=article_count(db),
        people=group_count(db),
        last_successful_run_at=last_successful_operation_at(db),
        checked_at=datetime.now(UTC),
    )
