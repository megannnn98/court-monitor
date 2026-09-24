"""Source-level case reports, including unnamed targets, with matching Excel export."""

from datetime import date, datetime, timedelta
from html import escape
from typing import Literal
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from channel_feed.case_age import AGE_LABELS, CaseAge
from channel_feed.case_reports import MOSCOW, load_case_reports
from web.candidate_rows import _CANDIDATE_CATEGORIES
from web.case_export import case_reports_xlsx
from web.dependencies import get_db
from web.ui.layout import _page

router = APIRouter()
type AgeFilter = Literal["all", "new", "update", "unknown"]


def _period(date_from: date | None, date_to: date | None) -> tuple[date, date]:
    end = date_to or datetime.now(MOSCOW).date()
    start = date_from or end - timedelta(days=45)
    if start > end or (end - start).days > 366:
        raise HTTPException(422, "Период должен быть от 0 до 366 дней")
    return start, end


@router.get("/ui/cases", response_class=HTMLResponse)
def ui_cases(
    date_from: date | None = None,
    date_to: date | None = None,
    age: AgeFilter = "all",
    unnamed_only: bool = Query(default=False),
    limit: int = Query(default=200, ge=1, le=1000),
    db: Session = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    start, end = _period(date_from, date_to)
    reports = load_case_reports(
        db,
        period_start=start,
        period_end=end,
        age=None if age == "all" else CaseAge(age),
        unnamed_only=unnamed_only,
    )
    options = "".join(
        f'<option value="{key}" {"selected" if key == age else ""}>{label}</option>'
        for key, label in [("all", "Все"), *AGE_LABELS.items()]
    )
    rows = []
    for item in reports[:limit]:
        source = escape(item.source)
        if item.url.startswith(("http://", "https://")):
            source = f'<a href="{escape(item.url, quote=True)}">{source}</a>'
        rows.append(f"""<tr>
<td>{escape(AGE_LABELS[item.timing.age])}<br><small>{escape(item.timing.reason)}</small></td>
<td>{escape("; ".join(item.names)) if item.names else "Имя не определено"}</td>
<td>{item.published.strftime("%d.%m.%Y")}</td>
<td>{escape(_CANDIDATE_CATEGORIES.get(item.event_type, item.event_type))}</td>
<td>{escape(item.timing.evidence) or "—"}</td>
<td>{escape(item.quotation)}<br><small>{escape(item.basis)}</small></td>
<td>{source}<br>{escape(item.title)}</td>
</tr>""")
    filters = urlencode(
        {
            "date_from": start.isoformat(),
            "date_to": end.isoformat(),
            "age": age,
            "unnamed_only": int(unnamed_only),
            "limit": limit,
        }
    )
    return _page(
        "Дела и события",
        f"""<form method="get" class="toolbar">
<label>Новости с <input type="date" name="date_from" value="{start}"></label>
<label>по <input type="date" name="date_to" value="{end}"></label>
<label>Давность дела <select name="age">{options}</select></label>
<label><input type="checkbox" name="unnamed_only" value="1" {"checked" if unnamed_only else ""}> Имя не определено</label>
<label>Строк <input type="number" name="limit" min="1" max="1000" value="{limit}"></label>
<button>Показать</button>
<a href="/ui/cases/export.xlsx?{escape(filters)}">Скачать эти строки в Excel</a>
</form>
<p>Найдено сообщений: {len(reports)}. Показано: {min(len(reports), limit)}.</p>
<p class="muted">Новое дело — в цитате сообщается о возбуждении в выбранном периоде.
Обновление старого — в цитате есть событие до начала периода.
Если времени недостаточно, давность неизвестна. Дата публикации не заменяет дату события.</p>
<p class="muted">Одна строка — сообщение об уголовном событии с тематическими признаками,
а не подтверждённое отдельное дело. Разные сообщения об одном деле могут повторяться.
«Имя не определено» означает, что у события нет извлечённого имени фигуранта.
Наличие имени в другой части статьи нужно проверить по источнику.</p>
<table><thead><tr><th>Давность</th><th>Фигурант</th><th>Дата новости</th><th>Событие</th>
<th>Время в цитате</th><th>Цитата и основание отбора</th><th>Источник</th></tr></thead>
<tbody>{"".join(rows) or '<tr><td colspan="7">По выбранным условиям сообщений нет.</td></tr>'}</tbody></table>""",
        active="cases",
        instruction="Новые дела, сообщения по старым делам и события без имени.",
        next_action="Проверьте цитату, фактичность события и фигуранта по ссылке на источник.",
        db=db,
    )


@router.get("/ui/cases/export.xlsx")
def export_cases(
    date_from: date | None = None,
    date_to: date | None = None,
    age: AgeFilter = "all",
    unnamed_only: bool = Query(default=False),
    limit: int = Query(default=200, ge=1, le=1000),
    db: Session = Depends(get_db),  # noqa: B008
) -> Response:
    start, end = _period(date_from, date_to)
    reports = load_case_reports(
        db,
        period_start=start,
        period_end=end,
        age=None if age == "all" else CaseAge(age),
        unnamed_only=unnamed_only,
    )
    return Response(
        case_reports_xlsx(reports[:limit]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="case-reports.xlsx"'},
    )
