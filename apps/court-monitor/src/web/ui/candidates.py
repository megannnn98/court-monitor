"""Operator console: candidates page and its CSV, Excel and PDF exports."""

from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from web.candidate_rows import (
    news_period_start,
    read_candidate_rows,
)
from web.dependencies import get_db
from web.exports import XLSX, candidates_xlsx
from web.ui.layout import HOME

router = APIRouter()


@router.get("/")
@router.get("/ui")
def ui_root() -> RedirectResponse:
    """The site opens on its home page, «Управление»."""
    return RedirectResponse(HOME, status_code=303)


@router.get("/ui/candidates/export.xlsx")
def ui_candidates_export_xlsx(
    snapshot_id: int = Query(..., ge=1),
    min_confidence: float = Query(default=0.7, ge=0.0, le=1.0),
    date_from: str | None = Query(default=None),
    include_administrative: bool = Query(default=False),
    criminal_only: bool = Query(default=False),
    event_date_filter: bool = Query(default=True),
    db: Session = Depends(get_db),  # noqa: B008
) -> Response:
    """The page's candidates in the page's order; the page `limit` is deliberately not applied."""
    rows = read_candidate_rows(
        db,
        snapshot_id=snapshot_id,
        min_confidence=min_confidence,
        period_start=news_period_start(date_from),
        include_administrative=include_administrative,
        criminal_only=criminal_only,
        event_date_filter=event_date_filter,
    )
    return Response(
        content=candidates_xlsx(rows),
        media_type=XLSX,
        headers={
            "Content-Disposition": f'attachment; filename="political-candidates-{snapshot_id}.xlsx"'
        },
    )
