"""Operator console: candidates page and its CSV, Excel and PDF exports."""

from html import escape
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from web.candidate_rows import (
    _CANDIDATE_CATEGORIES,
    _candidate_filters,
    _candidate_rows,
    _news_day,
    _period_start,
    _surname_first,
)
from web.dependencies import get_db
from web.exports import PdfFontMissingError, candidates_csv, candidates_pdf, candidates_xlsx
from web.routers.candidates import list_candidates
from web.routers.rosfinmonitoring import list_rosfinmonitoring_snapshots
from web.ui.layout import _page

router = APIRouter()


@router.get("/ui/candidates")
def ui_candidates(
    snapshot_id: int | None = Query(default=None, ge=1),
    min_confidence: float = Query(default=0.7, ge=0.0, le=1.0),
    limit: int = Query(default=100, ge=1, le=1000),
    date_from: str | None = Query(default=None),
    include_administrative: bool = Query(default=False),
    db: Session = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    snapshots = list_rosfinmonitoring_snapshots(limit=20, db=db)
    selected_snapshot_id = snapshot_id or (snapshots[0].id if snapshots else None)
    if selected_snapshot_id is None:
        return _page(
            "Кандидаты",
            '<p class="muted">Snapshot Росфинмониторинга ещё не загружен.</p>',
            active="candidates",
            instruction="Здесь люди с политической классификацией и подтверждённым отсутствием в выбранном snapshot РФМ.",
            next_action="Импортируйте snapshot Росфинмониторинга через CLI, затем вернитесь сюда.",
            db=db,
            warning="Без snapshot нельзя отличить подтверждённое отсутствие от отсутствия проверки.",
        )

    period_start = _period_start(date_from)
    try:
        candidate_rows = _candidate_rows(
            db,
            snapshot_id=selected_snapshot_id,
            min_confidence=min_confidence,
            period_start=period_start,
            include_administrative=include_administrative,
        )
    except HTTPException as exc:
        if exc.status_code != 404:
            raise
        candidate_rows = []

    snapshot_options = "".join(
        f'<option value="{item.id}" {"selected" if item.id == selected_snapshot_id else ""}>'
        f"{item.id} — {escape(item.snapshot_date)} ({item.entry_count:,})</option>"
        for item in snapshots
    )
    rows = "".join(
        f"""<tr>
  <td>{position}</td>
  <td><a href="/ui/persons/{candidate.person_id}">{candidate.person_id}</a></td>
  <td><a href="/ui/persons/{candidate.person_id}">{escape(_surname_first(candidate.canonical_name))}</a></td>
  <td>{_news_day(news.published_at).strftime("%d.%m.%Y") if news and news.published_at else ""}</td>
  <td>{escape(_CANDIDATE_CATEGORIES.get(news.event_type, news.event_type)) if news and news.event_type else ""}</td>
  <td>{candidate.persecution_confidence:.2f}</td>
  <td>{candidate.event_count}</td>
  <td>{escape(candidate.rosfinmonitoring_status)}</td>
  <td>{escape(", ".join(candidate.persecution_reasons))}</td>
</tr>"""
        for position, (candidate, news) in enumerate(candidate_rows[:limit], start=1)
    )
    legacy_filters = urlencode(
        {"snapshot_id": selected_snapshot_id, "min_confidence": min_confidence, "limit": limit}
    )
    filters = _candidate_filters(
        selected_snapshot_id, min_confidence, period_start, include_administrative
    )
    period_value = period_start.isoformat() if period_start is not None else ""
    administrative_checked = "checked" if include_administrative else ""
    return _page(
        "Кандидаты",
        f"""<form method="get" class="toolbar">
  <label>Snapshot РФМ <select name="snapshot_id">{snapshot_options}</select></label>
  <label>Min confidence <input type="number" name="min_confidence" min="0" max="1" step="0.05" value="{min_confidence}"></label>
  <label>Limit <input type="number" name="limit" min="1" max="1000" value="{limit}"></label>
  <label>Новости с <input type="date" name="date_from" value="{period_value}"></label>
  <label><input type="checkbox" name="include_administrative" value="1" {administrative_checked}> Включая административные</label>
  <button>Обновить</button>
  <a class="secondary" href="/ui/candidates/export?{legacy_filters}">Скачать CSV</a>
  <a class="secondary" href="/ui/candidates/export.pdf?{legacy_filters}">Скачать PDF</a>
  <a class="secondary" href="/ui/candidates/export.xlsx?{filters}">Export to Excel</a>
</form>
<p class="muted">Найдено: {len(candidate_rows)}, показано: {min(len(candidate_rows), limit)}. Статус РФМ: <code>not_matched</code>. Сначала новые дела, аресты и приговоры, затем по дате новости.</p>
<table><thead><tr><th>№</th><th>Person ID</th><th>Персона</th><th>Дата новости</th><th>Категория</th><th>Political confidence</th><th>Events</th><th>RF status</th><th>Причины</th></tr></thead><tbody>{rows}</tbody></table>""",
        active="candidates",
        instruction="Кандидаты — политически классифицированные люди с подтверждённым статусом РФМ not_matched.",
        next_action="Откройте Person, проверьте события и evidence spans в исходных статьях.",
        db=db,
    )


@router.get("/ui/candidates/export")
def ui_candidates_export(
    snapshot_id: int = Query(..., ge=1),
    min_confidence: float = Query(default=0.7, ge=0.0, le=1.0),
    limit: int = Query(default=100, ge=1, le=1000),
    db: Session = Depends(get_db),  # noqa: B008
) -> Response:
    candidates = list_candidates(
        snapshot_id=snapshot_id,
        min_persecution_confidence=min_confidence,
        limit=limit,
        db=db,
    )
    return Response(
        content=candidates_csv(candidates),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="political-candidates-{snapshot_id}.csv"'
        },
    )


@router.get("/ui/candidates/export.xlsx")
def ui_candidates_export_xlsx(
    snapshot_id: int = Query(..., ge=1),
    min_confidence: float = Query(default=0.7, ge=0.0, le=1.0),
    date_from: str | None = Query(default=None),
    include_administrative: bool = Query(default=False),
    db: Session = Depends(get_db),  # noqa: B008
) -> Response:
    """The page's candidates in the page's order; the page `limit` is deliberately not applied."""
    rows = _candidate_rows(
        db,
        snapshot_id=snapshot_id,
        min_confidence=min_confidence,
        period_start=_period_start(date_from),
        include_administrative=include_administrative,
    )
    return Response(
        content=candidates_xlsx(rows),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="political-candidates-{snapshot_id}.xlsx"'
        },
    )


@router.get("/ui/candidates/export.pdf")
def ui_candidates_export_pdf(
    snapshot_id: int = Query(..., ge=1),
    min_confidence: float = Query(default=0.7, ge=0.0, le=1.0),
    limit: int = Query(default=100, ge=1, le=1000),
    db: Session = Depends(get_db),  # noqa: B008
) -> Response:
    candidates = list_candidates(
        snapshot_id=snapshot_id,
        min_persecution_confidence=min_confidence,
        limit=limit,
        db=db,
    )
    try:
        content = candidates_pdf(candidates, snapshot_id=snapshot_id, min_confidence=min_confidence)
    except PdfFontMissingError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return Response(
        content=content,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="political-candidates-{snapshot_id}.pdf"'
        },
    )
