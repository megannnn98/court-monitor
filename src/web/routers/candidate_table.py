"""The candidates' table for the React console, read as the legacy page and its Excel
file read it (`select_candidate_rows`): the same period, administrative cases, event
dates and order, so a row on the page is a row in the file. `/api/v1/candidates` stays
the service's own list, a mirror of the legacy JSON."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from web.candidate_rows import (
    CANDIDATE_CATEGORIES,
    latest_snapshot_id,
    news_day,
    news_period_start,
    read_candidate_rows,
    surname_first,
)
from web.dependencies import get_db
from web.response_models import CandidateRowResponse, CandidateTableResponse

router = APIRouter()


@router.get("/candidates/table", response_model=CandidateTableResponse)
def candidate_table(
    snapshot_id: int | None = Query(default=None, ge=1),
    min_confidence: float = Query(default=0.7, ge=0.0, le=1.0),
    limit: int = Query(default=100, ge=1, le=1000),
    date_from: str | None = Query(default=None),
    include_administrative: bool = Query(default=False),
    criminal_only: bool = Query(default=False),
    event_date_filter: bool = Query(default=True),
    db: Session = Depends(get_db),  # noqa: B008
) -> CandidateTableResponse:
    """The legacy `/ui/candidates` table: the newest snapshot unless one is named; news of
    the last 45 days unless `date_from` says otherwise (empty: every day)."""
    selected = snapshot_id or latest_snapshot_id(db)
    if selected is None:
        raise HTTPException(status_code=404, detail="Snapshot Росфинмониторинга ещё не загружен.")
    start = news_period_start(date_from)
    rows = read_candidate_rows(
        db,
        snapshot_id=selected,
        min_confidence=min_confidence,
        period_start=start,
        include_administrative=include_administrative,
        criminal_only=criminal_only,
        event_date_filter=event_date_filter,
    )
    return CandidateTableResponse(
        snapshot_id=selected,
        period_start=start,
        total=len(rows),
        items=[
            CandidateRowResponse(
                person_id=candidate.person_id,
                name=surname_first(candidate.canonical_name),
                news_day=news_day(news.published_at) if news and news.published_at else None,
                category=(
                    CANDIDATE_CATEGORIES.get(news.event_type, news.event_type)
                    if news and news.event_type
                    else None
                ),
                persecution_confidence=candidate.persecution_confidence,
                event_count=candidate.event_count,
                rosfinmonitoring_status=candidate.rosfinmonitoring_status,
                reasons=list(candidate.persecution_reasons),
            )
            for candidate, news in rows[:limit]
        ],
    )
