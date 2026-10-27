"""«Результат» as an Excel file: every row of the list's filters, not only one page.

The React page links here (`export_url` of `/api/v1/political`), with its filters in the
address."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from web.dependencies import get_db
from web.exports import XLSX
from web.views.political import export_name, political_xlsx
from web.views.political_filters import filters
from web.views.political_rows import result_rows, with_details

router = APIRouter()


@router.get("/ui/political/export.xlsx")
def ui_political_export(
    months: int = Query(default=0),
    date_from: str = Query(default="", max_length=10),
    date_to: str = Query(default="", max_length=10),
    news: str = Query(default="all", max_length=16),
    known: str = Query(default="all", max_length=16),
    done: str = Query(default="hide", max_length=8),
    who: str = Query(default="all", max_length=8),
    rfm: str = Query(default="all", max_length=8),
    db: Session = Depends(get_db),  # noqa: B008
) -> Response:
    """Every row of the list's filters, not only one page."""
    chosen = filters(months, date_from, date_to, news, known, done, who, rfm)
    result = result_rows(db, chosen)
    name = export_name(chosen, result.rows, datetime.now(UTC).date())
    return Response(
        political_xlsx(with_details(db, result.rows), known_loaded=bool(result.base_size)),
        media_type=XLSX,
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )
