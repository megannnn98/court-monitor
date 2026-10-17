"""What «О системе» reads: the build stamp, the totals behind the status strip, and the
last successful run. Shared by the legacy page and `GET /api/v1/about`, so both say the
same thing. Read-only."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import TextClause

from db.orm_models import EntityGroupRecord, ParsedArticleRecord
from web.build_info import build_info
from web.response_models import AboutResponse

__all__ = ["read_about"]


def _count(table: str) -> TextClause:
    """A COUNT over one table by name. The names are literals in this module, never
    anything a request can reach."""
    return text(f"SELECT count(*) FROM {table}")


def _last_run(db: Session) -> datetime | None:
    row = db.execute(
        text(
            """
            SELECT started_at FROM operator_operation_runs
            WHERE status = 'succeeded' ORDER BY id DESC LIMIT 1
            """
        )
    ).first()
    if row is None or row[0] is None:
        return None
    started: datetime = row[0]
    return started if started.tzinfo is not None else started.replace(tzinfo=UTC)


def read_about(db: Session) -> AboutResponse:
    """The build this instance runs and the data it is looking at, as of now."""
    info = build_info()
    return AboutResponse(
        version=info.version,
        tag=info.tag,
        commit=info.commit,
        built_at=info.built_at,
        articles=db.scalar(_count(ParsedArticleRecord.__tablename__)) or 0,
        people=db.scalar(_count(EntityGroupRecord.__tablename__)) or 0,
        last_successful_run_at=_last_run(db),
        checked_at=datetime.now(UTC),
    )
