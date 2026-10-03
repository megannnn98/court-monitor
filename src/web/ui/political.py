"""«Результат»: the politically persecuted — what the whole pipeline is for.

A figurant of a criminal case (step 4) whose case is political persecution (step 5), on
the Rosfinmonitoring list or not: the list does not make a case known, it confirms who a
person is (the operator's word) — its birth date and place are shown beside the name. A
name the list carries without a patronymic may be a namesake: marked, and can be hidden.
The period filter answers «new or old case» by the date of the latest publication; the
same rows go to Excel.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from html import escape
from urllib.parse import parse_qs, urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from openpyxl import Workbook
from sqlalchemy.orm import Session

from db.orm_models import (
    EntityDoneMarkRecord,
)
from entities.known_base import COUNTED, KnownMatch
from entities.news import KIND_LABELS, NEW_CASE, ONGOING, SENTENCE, UNKNOWN
from entities.rf_check import FULL
from entities.rf_entry import (
    INCLUSION_NOTES,
    entry_included_text,
    rf_word,
)
from persecution.classifier import POLITICAL_ARTICLES
from rosfinmonitoring.inclusion_dates import ATTRIBUTION as INCLUSION_ATTRIBUTION
from web.dependencies import get_db
from web.exports import (
    XLSX,
    excel_day,
    is_web_link,
    workbook_bytes,
    write_notes,
    write_sheet,
)
from web.ui.entities import _date
from web.ui.funnel import funnel, funnel_line
from web.ui.layout import _page, copy_button, pager
from web.ui.political_filters import (
    FILTER_NAMES,
    FILTERS_COOKIE,
    KNOWN_FILTERS,
    NEWS_FILTERS,
    PERIODS,
    WHO_FILTERS,
    Filters,
    date_field,
    filters,
    remembered,
)
from web.ui.political_rows import LINKS, ListRow, latest_news, result_rows, with_details

router = APIRouter()

PAGE_SIZE = 100


def _article_text(articles: list[tuple[str, bool]]) -> str:
    return ", ".join(article for article, _ in articles)


# Who said so. A person who decides a verdict by hand is not the model, and telling the
# operator their own decision came from the model costs them the reason to trust it.
_BASIS_LABELS = {
    "article": "статья УК",
    "manual": "решено вручную",
    "memorial": "правило: реестр «Мемориала»",
    "model": "модель",
}


# What `decide_politics` writes as the reason. The label already says this, so printing
# it after a colon reads as «решено вручную: решено оператором вручную».
_MANUAL_REASON = "решено оператором вручную"


def _basis(row: ListRow) -> str:
    """Who decided the verdict, and why — the two never say the same thing twice."""
    label = _BASIS_LABELS.get(row.basis.method, "модель")
    reason = row.basis.reason
    if reason in ("", _MANUAL_REASON):
        return label
    return f"{label}: {reason}"


def _news_mark(row: ListRow) -> str:
    """The latest news as a mark; the new cases and the sentences stand out."""
    if row.news_kind is None:
        return '<span class="muted">—</span>'
    css = {NEW_CASE: "succeeded", SENTENCE: "succeeded", UNKNOWN: "pending"}.get(row.news_kind, "")
    return (
        f'<span class="badge {css}" title="{escape(row.news_reason, quote=True)}">'
        f"{escape(KIND_LABELS.get(row.news_kind, row.news_kind))}</span>"
    )


def _known_mark(row: ListRow, *, loaded: bool) -> str:
    """What the operator's base says of the person; the names it holds in the tooltip."""
    if not loaded:
        return '<span class="muted">—</span>'
    match = row.known
    if match is None:
        return '<span class="badge succeeded">нет в базе</span>'
    several = match.level in COUNTED
    names = "; ".join(match.names)
    return (
        f'<span class="badge {"pending" if several else ""}" title="{escape(names, quote=True)}">'
        f"{escape(match.label)}</span>"
        f'<br><span class="muted">{escape("" if match.level == "namesakes" else names)}</span>'
    )


def _done_box(row: ListRow, back: str) -> str:
    """The tick at the start of a row: «обработано». Ticking sends the form at once."""
    return (
        '<form method="post" action="/ui/political/done" class="inline-form">'
        f'<input type="hidden" name="key" value="{escape(row.key, quote=True)}">'
        f'<input type="hidden" name="back" value="{escape(back, quote=True)}">'
        f'<input type="hidden" name="done" value="{0 if row.done else 1}">'
        '<input type="checkbox" title="Обработано" aria-label="Обработано" '
        # requestSubmit, not submit: it fires the event the scroll keeper listens for.
        f'onchange="this.form.requestSubmit()"{" checked" if row.done else ""}></form>'
    )


def _html_row(position: int, row: ListRow, *, known_loaded: bool = False, back: str = "") -> str:
    articles = ", ".join(
        f"<b>{escape(article)}</b>" if article in POLITICAL_ARTICLES else escape(article)
        for article, _ in row.articles
    )
    links = "".join(
        f'<div class="pub-link"><span class="muted">{escape(source)}:</span> '
        f'<a href="{escape(url, quote=True)}">{escape(title[:80])}</a></div>'
        for title, url, _, source in row.links
        if is_web_link(url)
    )
    listed = (
        ' <span class="badge">в перечне РФМ</span>'
        if row.rf_level == FULL
        else ' <span class="badge pending">возможно в перечне</span>'
        if row.maybe_listed
        else ""
    )
    nameless = (
        ' <span class="badge pending" title="Публикация не называет имени">без имени</span>'
        if row.unnamed
        else ""
    )
    # Shown only for an entry matched with the patronymic: on a namesake the day belongs
    # to somebody else, so it is not shown at all.
    included = (
        f'<div class="muted">{escape(entry_included_text(row.rf_inclusion_date))}</div>'
        if row.rf_level == FULL and row.rf_inclusion_date
        else ""
    )
    return (
        f"<tr{' class="done"' if row.done else ''}><td>{_done_box(row, back)}</td>"
        f"<td>{position}</td>"
        f'<td><a href="{escape(row.href, quote=True)}">{escape(row.shown_name)}</a>'
        f"{listed}{nameless} {copy_button(row.shown_name)}{included}</td>"
        f"<td>{_news_mark(row)}</td>"
        f"<td>{_known_mark(row, loaded=known_loaded)}</td>"
        f"<td>{escape(row.regions)}</td>"
        f'<td class="muted">{escape(row.rf_entry)}</td>'
        f"<td>{articles}</td>"
        f'<td>{escape(_basis(row))}<br><span class="muted">{escape(row.basis.quote[:200])}</span></td>'
        f"<td>{escape(row.memorial or '')}</td>"
        f"<td>{escape(_date(row.first_published))}</td>"
        f"<td>{escape(_date(row.last_published))}</td>"
        f"<td>{links}</td></tr>"
    )


@router.get("/ui/political", response_class=HTMLResponse)
def ui_political(
    request: Request,
    months: int = Query(default=0),
    date_from: str = Query(default="", max_length=10),
    date_to: str = Query(default="", max_length=10),
    news: str = Query(default="all", max_length=16),
    known: str = Query(default="all", max_length=16),
    done: str = Query(default="hide", max_length=8),
    who: str = Query(default="all", max_length=8),
    page: int = Query(default=1, ge=1),
    db: Session = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    chosen = filters(months, date_from, date_to, news, known, done, who)
    # No filters in the address: the last ones chosen, not «all the time».
    if not any(name in request.query_params for name in FILTER_NAMES):
        chosen = remembered(request.cookies.get(FILTERS_COOKIE, "")) or chosen
    result = result_rows(db, chosen)
    total = len(result.rows)
    on_page = with_details(db, result.rows[(page - 1) * PAGE_SIZE : page * PAGE_SIZE])
    keep = chosen.query()
    back = urlencode({**keep, "page": page})
    rows = "".join(
        _html_row(position, row, known_loaded=bool(result.base_size), back=back)
        for position, row in enumerate(on_page, start=(page - 1) * PAGE_SIZE + 1)
    )
    who_options = "".join(
        f'<option value="{key}"{" selected" if key == chosen.who else ""}>{label}</option>'
        for key, label in WHO_FILTERS.items()
    )
    # Beside the filters and always there, so the way back to a ticked person is in plain
    # sight: the first person to tick a row could not find them again.
    done_toggle = (
        # `check`, the tick-box label: `field` is the select's, and sizes the box as one.
        '<label class="check"><input type="checkbox" name="done" value="show" '
        f'onchange="this.form.submit()"{" checked" if chosen.done == "show" else ""}> '
        f"Показать обработанных ({result.done_total})</label>"
    )
    # A period button clears the dates: the months are the choice then.
    periods = " ".join(
        f'<button type="submit" name="months" value="{key}" '
        f'class="chip{" active" if key == chosen.months and not chosen.custom else ""}" '
        "onclick=\"this.form.date_from.value='';this.form.date_to.value=''\">"
        f"{label}</button>"
        for key, label in PERIODS.items()
    )
    pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
    pages_html = pager("/ui/political", keep, page, pages)
    news_options = "".join(
        f'<option value="{key}"{" selected" if key == chosen.news else ""}>{escape(label)}'
        f"{f' ({result.news_counts.get(key, 0)})' if key != 'all' else ''}</option>"
        for key, label in NEWS_FILTERS.items()
        if key in ("all", NEW_CASE, SENTENCE, ONGOING) or result.news_counts.get(key)
    )
    known_options = "".join(
        f'<option value="{key}"{" selected" if key == chosen.known else ""}>{escape(label)}'
        f"{f' ({result.known_counts.get(key, 0)})' if key != 'all' else ''}</option>"
        for key, label in KNOWN_FILTERS.items()
    )
    known_select = (
        '<label class="field">В базе Airtable <select name="known" '
        f'onchange="this.form.submit()">{known_options}</select></label>'
        if result.base_size
        else ""
    )
    dates = (
        date_field("date_from", "с", chosen.date_from)
        + date_field("date_to", "по", chosen.date_to)
        + '<button type="submit">Показать</button>'
    )
    # The chosen months ride along hidden; a period button, sent later, wins over them.
    body = f"""<form method="get" action="/ui/political" class="toolbar" id="political-filters">
  <input type="hidden" name="months" value="{chosen.months}">
  <span class="chips">{periods}</span>
  <span class="chips">{dates}</span>
  <div class="filter-row">
    <!-- The form's own values, not the page's: dates picked but not shown yet count. -->
    <label class="field">Свежая новость <select name="news" onchange="this.form.submit()">{
        news_options
    }</select></label>
    {known_select}
    <label class="field">Кто <select name="who" onchange="this.form.submit()">{
        who_options
    }</select></label>
    {done_toggle}
    <button type="submit" class="secondary" formaction="/ui/political/export.xlsx">Скачать Excel</button>
  </div>
</form>
{funnel_line(funnel(db))}
<p class="muted">Найдено: {total}. Галочка в начале строки — «обработано»: человек
уходит из списка и вернётся, когда о нём появится новая новость; «Показать обработанных»
над таблицей возвращает их в список, чтобы снять галочку. «Без имени» — фигуранты, которых
публикация не называет: в списке только те, чьё дело идёт по политической статье; несколько
упоминаний одного человека сведены в одну строку по возрасту, полу и месту. Фигуранты уголовных дел, дело которых — политическое
преследование. Перечень Росфинмониторинга подтверждает личность: дата рождения и место из
него — рядом с именем; «возможно в перечне» — совпали только имя и фамилия, может быть тёзка.
Дата включения — свойство записи перечня, а не человека: под именем она читается как
«запись перечня включена …» и не делает совпадение подтверждением личности.
{escape(INCLUSION_ATTRIBUTION)}.
Жирная статья — из списка политических; «Последняя новость» показывает, свежий ли случай.
«В базе Airtable» — есть ли человек в вашей таблице «Найденные люди». Названных сверяем по
имени, поэтому «вероятно» и «тёзки» — не уверенность. Безымянных — с записями базы без имени:
по возрасту в названии записи, полу и региону; «вероятно» — подошла одна запись, «похожие
записи» — несколько.</p>
<table><thead><tr><th title="Обработано">✓</th><th>№</th><th>Фамилия Имя</th><th>Свежая новость</th><th>В базе Airtable</th><th>Регион</th><th>Перечень РФМ</th><th>Статьи УК</th>
<th>Почему политическое</th><th>Мемориал</th><th>Первая новость</th><th>Последняя новость</th>
<th>Публикации</th></tr></thead><tbody>{rows}</tbody></table>
{pages_html}
<script>
// The calendar: opened by its button, the day it gives written day/month/year.
document.querySelectorAll("#political-filters .date-pick").forEach((pick) => {{
  const field = pick.querySelector("[data-date]");
  const native = pick.querySelector(".date-native");
  pick.querySelector(".date-open").addEventListener("click", () => {{
    const [d, m, y] = field.value.split("/");
    native.value = y && m && d ? `${{y}}-${{m.padStart(2, "0")}}-${{d.padStart(2, "0")}}` : "";
    try {{ native.showPicker(); }} catch {{ native.focus(); }}
  }});
  native.addEventListener("change", () => {{
    if (!native.value) return;
    const [y, m, d] = native.value.split("-");
    field.value = `${{d}}/${{m}}/${{y}}`;
    field.dispatchEvent(new Event("change", {{ bubbles: true }}));
  }});
}});
// Dates picked but not shown yet are kept too: a reload shows them.
document.getElementById("political-filters").addEventListener("change", (event) => {{
  if (!("date" in event.target.dataset)) return;
  const form = new URLSearchParams(new FormData(event.target.form));
  document.cookie = "{FILTERS_COOKIE}=" + encodeURIComponent(form.toString()) +
    "; path=/ui/political; max-age=31536000; samesite=lax";
}});
</script>"""
    response = _page(
        "Результат",
        body,
        active="political",
        instruction=(
            "Люди, против которых заведены политические уголовные дела; перечень "
            "Росфинмониторинга подтверждает их личность."
        ),
        db=db,
    )
    response.set_cookie(
        FILTERS_COOKIE,
        urlencode(chosen.query()),
        max_age=365 * 24 * 3600,
        path="/ui/political",
        samesite="lax",
    )
    return response


def known_answer_text(match: KnownMatch | None, *, loaded: bool) -> str | None:
    """The answer of the operator's base as a cell: what, and which records."""
    if not loaded:
        return None
    if match is None:
        return "нет в базе"
    names = "; ".join(match.names)
    return f"{match.label} ({names})" if match.level in COUNTED else f"{match.label}: {names}"


def _known_text(row: ListRow, *, loaded: bool) -> str | None:
    return known_answer_text(row.known, loaded=loaded)


def political_xlsx(rows: list[ListRow], *, known_loaded: bool = False) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.title = "Результат"
    headers = [
        "№",
        "Фамилия Имя",
        "Регион",
        "Статьи УК",
        "Почему политическое",
        "Мемориал",
        "Первая новость",
        "Последняя новость",
        "Перечень РФМ",
        *(f"Источник {number}" for number in range(1, LINKS + 1)),
        "Свежая новость",
        "В базе Airtable",
    ]
    first_source = headers.index("Источник 1") + 1
    sources = [
        [(title, url, source) for title, url, _, source in row.links if is_web_link(url)][:LINKS]
        for row in rows
    ]
    write_sheet(
        sheet,
        headers,
        [
            [
                position,
                row.shown_name,
                row.regions or None,
                _article_text(row.articles) or None,
                _basis(row),
                row.memorial,
                excel_day(row.first_published),
                excel_day(row.last_published),
                rf_word(row.rf_level, row.rf_entry, row.rf_inclusion_date) or None,
                *(f"{source}: {title[:80]}" for title, _, source in found),
                *(None for _ in range(LINKS - len(found))),
                KIND_LABELS.get(row.news_kind, row.news_kind) if row.news_kind else None,
                _known_text(row, loaded=known_loaded),
            ]
            for position, (row, found) in enumerate(zip(rows, sources, strict=True), start=1)
        ],
    )
    for line, found in enumerate(sources, start=2):
        for column, (_, url, _) in enumerate(found, start=first_source):
            sheet.cell(row=line, column=column).hyperlink = url
    write_notes(workbook, "Источник дат", INCLUSION_NOTES)
    return workbook_bytes(workbook)


def export_name(chosen: Filters, rows: list[ListRow], today: date) -> str:
    """`result_<from>_<to>.xlsx`: the dates chosen, else the period's start, else the
    earliest latest news of the rows; up to the date chosen, else today."""
    start = chosen.date_from
    if start is None and chosen.months:
        start = today - timedelta(days=30 * chosen.months)
    if start is None:
        dates = [row.last_published for row in rows if row.last_published is not None]
        start = min(dates).date() if dates else today
    end = chosen.date_to or today
    return f"result_{start.isoformat()}_{end.isoformat()}.xlsx"


@router.get("/ui/political/export.xlsx")
def ui_political_export(
    months: int = Query(default=0),
    date_from: str = Query(default="", max_length=10),
    date_to: str = Query(default="", max_length=10),
    news: str = Query(default="all", max_length=16),
    known: str = Query(default="all", max_length=16),
    done: str = Query(default="hide", max_length=8),
    who: str = Query(default="all", max_length=8),
    db: Session = Depends(get_db),  # noqa: B008
) -> Response:
    """Every row of the page's filters, not only one page."""
    chosen = filters(months, date_from, date_to, news, known, done, who)
    result = result_rows(db, chosen)
    name = export_name(chosen, result.rows, datetime.now(UTC).date())
    return Response(
        political_xlsx(with_details(db, result.rows), known_loaded=bool(result.base_size)),
        media_type=XLSX,
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@router.post("/ui/political/done")
async def ui_political_done(
    request: Request,
    db: Session = Depends(get_db),  # noqa: B008
) -> RedirectResponse:
    """Tick or untick «обработано» on a person, and come back to the same list."""
    form = {
        name: values[0]
        for name, values in parse_qs((await request.body()).decode("utf-8", "replace")).items()
    }
    key = form.get("key", "")
    try:
        seen = latest_news(db, key)
    except LookupError:
        raise HTTPException(status_code=404, detail="Человек не найден") from None
    mark = db.get(EntityDoneMarkRecord, key)
    if form.get("done") == "1":
        if mark is None:
            mark = EntityDoneMarkRecord(key=key)
            db.add(mark)
        # The news the operator has seen: a later one brings the person back.
        mark.news_at = seen
    elif mark is not None:
        db.delete(mark)
    db.commit()
    # Only the list's own address: the form's `back` is its query, never a place to go.
    return RedirectResponse(
        f"/ui/political?{urlencode(parse_qs(form.get('back', '')), doseq=True)}", 303
    )
