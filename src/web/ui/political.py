"""«Список»: the politically persecuted who are not on the Rosfinmonitoring list.

A figurant of a criminal case (step 5), off the list (step 4), whose case is political
persecution (step 6). A name the list may carry without a patronymic stays, marked. The
period filter answers «new or old case» by the date of the latest publication; the same
rows go to Excel.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from html import escape
from io import BytesIO
from typing import Any
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Depends, Query
from fastapi.responses import HTMLResponse, Response
from openpyxl import Workbook
from sqlalchemy import exists, func, select, text
from sqlalchemy.orm import Session

from db.orm_models import EntityGroupPoliticsRecord, EntityGroupRecord, EntityGroupRfMatchRecord
from entities.politics import MEMORIAL_CATEGORIES, POLITICAL
from persecution.classifier import POLITICAL_ARTICLES
from web.dependencies import get_db
from web.ui.entities import _articles_by_group, _date, display_name
from web.ui.layout import _page

router = APIRouter()

PAGE_SIZE = 100
LINKS = 3
PERIODS = {0: "За всё время", 1: "Месяц", 3: "3 месяца", 6: "Полгода", 12: "Год"}

_PUBLICATIONS = text(
    """
    SELECT DISTINCT gm.group_id, a.id, a.title, a.published_at, d.canonical_url
    FROM entity_group_mentions gm
    JOIN entity_mentions m ON m.id = gm.mention_id
    JOIN article_extraction_runs r ON r.id = m.extraction_run_id
    JOIN parsed_articles a ON a.id = r.article_id
    JOIN source_documents d ON d.id = a.document_id
    WHERE gm.group_id = ANY(:groups)
    """
)


@dataclass
class ListRow:
    entity: EntityGroupRecord
    politics: EntityGroupPoliticsRecord
    maybe_listed: bool
    articles: list[tuple[str, bool]] = field(default_factory=list)
    memorial: str | None = None
    first_published: datetime | None = None
    # (title, url, published) of the latest publications.
    links: list[tuple[str, str, datetime | None]] = field(default_factory=list)


@dataclass(frozen=True)
class Filters:
    """The list's filters: a period of the latest news — the last months, or dates — and
    whether to hide those the list may carry without a patronymic."""

    months: int = 0
    date_from: date | None = None
    date_to: date | None = None
    hide_maybe_listed: bool = False

    @property
    def custom(self) -> bool:
        return self.date_from is not None or self.date_to is not None

    def query(self) -> dict[str, str]:
        """As URL parameters, for the pager and the Excel link."""
        return {
            "months": str(self.months),
            "date_from": self.date_from.isoformat() if self.date_from else "",
            "date_to": self.date_to.isoformat() if self.date_to else "",
            "hide_maybe_listed": str(self.hide_maybe_listed).lower(),
        }


def _parse_date(text: str) -> date | None:
    try:
        return date.fromisoformat(text.strip()) if text.strip() else None
    except ValueError:
        return None


def filters(months: int, date_from: str, date_to: str, hide_maybe_listed: bool) -> Filters:
    """Dates, when given, win over the months."""
    start, end = _parse_date(date_from), _parse_date(date_to)
    return Filters(
        months=0 if start or end or months not in PERIODS else months,
        date_from=start,
        date_to=end,
        hide_maybe_listed=hide_maybe_listed,
    )


def _rows(
    db: Session, chosen: Filters
) -> tuple[list[tuple[EntityGroupRecord, EntityGroupPoliticsRecord]], int, int]:
    """The list's entities, latest news first; how many there are, and how many of them
    the list may carry under a name without a patronymic."""
    maybe_listed = exists().where(EntityGroupRfMatchRecord.group_id == EntityGroupRecord.id)
    query = (
        select(EntityGroupRecord, EntityGroupPoliticsRecord)
        .join(EntityGroupPoliticsRecord, EntityGroupPoliticsRecord.group_id == EntityGroupRecord.id)
        .where(EntityGroupPoliticsRecord.verdict == POLITICAL)
    )
    if chosen.months:
        since = datetime.now(UTC) - timedelta(days=30 * chosen.months)
        query = query.where(EntityGroupRecord.last_published_at >= since)
    if chosen.date_from is not None:
        start = datetime.combine(chosen.date_from, time.min, UTC)
        query = query.where(EntityGroupRecord.last_published_at >= start)
    if chosen.date_to is not None:
        # The whole last day.
        end = datetime.combine(chosen.date_to + timedelta(days=1), time.min, UTC)
        query = query.where(EntityGroupRecord.last_published_at < end)
    maybe = db.scalar(select(func.count()).select_from(query.where(maybe_listed).subquery())) or 0
    if chosen.hide_maybe_listed:
        query = query.where(~maybe_listed)
    rows = db.execute(
        query.order_by(
            EntityGroupRecord.last_published_at.desc().nulls_last(), EntityGroupRecord.key
        )
    ).all()
    return [(entity, politics) for entity, politics in rows], len(rows), maybe


def _details(
    db: Session, found: list[tuple[EntityGroupRecord, EntityGroupPoliticsRecord]]
) -> list[ListRow]:
    ids = [entity.id for entity, _ in found]
    listed = set(
        db.scalars(
            select(EntityGroupRfMatchRecord.group_id).where(
                EntityGroupRfMatchRecord.group_id.in_(ids)
            )
        )
    )
    rows = {
        entity.id: ListRow(entity=entity, politics=politics, maybe_listed=entity.id in listed)
        for entity, politics in found
    }
    for group_id, articles in _articles_by_group(db, ids).items():
        rows[group_id].articles = articles
    for group_id, category in db.execute(MEMORIAL_CATEGORIES, {"groups": ids}).all():
        rows[group_id].memorial = category
    publications: dict[int, list[tuple[str, str, datetime | None]]] = {}
    for group_id, _id, title, published_at, url in db.execute(_PUBLICATIONS, {"groups": ids}).all():
        publications.setdefault(group_id, []).append((title, url, published_at))
    oldest = datetime.min.replace(tzinfo=UTC)
    for group_id, found_publications in publications.items():
        dates = [published for _, _, published in found_publications if published is not None]
        rows[group_id].first_published = min(dates) if dates else None
        rows[group_id].links = sorted(
            found_publications, key=lambda item: item[2] or oldest, reverse=True
        )[:LINKS]
    return [rows[entity.id] for entity, _ in found]


def _article_text(articles: list[tuple[str, bool]]) -> str:
    return ", ".join(article for article, _ in articles)


def _regions_text(entity: EntityGroupRecord) -> str:
    return ", ".join(str(region) for region, _ in entity.regions)


def _basis(row: ListRow) -> str:
    method = "статья УК" if row.politics.method == "article" else "модель"
    return f"{method}: {row.politics.reason}"


def _html_row(position: int, row: ListRow) -> str:
    entity = row.entity
    articles = ", ".join(
        f"<b>{escape(article)}</b>" if article in POLITICAL_ARTICLES else escape(article)
        for article, _ in row.articles
    )
    links = "<br>".join(
        f'<a href="{escape(url, quote=True)}">{escape(title[:80])}</a>'
        for title, url, _ in row.links
        if url.startswith(("http://", "https://"))
    )
    maybe = ' <span class="badge pending">возможно в перечне</span>' if row.maybe_listed else ""
    return (
        f"<tr><td>{position}</td>"
        f'<td><a href="/ui/entities/{quote(entity.key)}">{escape(display_name(entity.name))}</a>'
        f"{maybe}</td>"
        f"<td>{escape(_regions_text(entity))}</td>"
        f"<td>{articles}</td>"
        f'<td>{escape(_basis(row))}<br><span class="muted">{escape(row.politics.quote[:200])}</span></td>'
        f"<td>{escape(row.memorial or '')}</td>"
        f"<td>{escape(_date(row.first_published))}</td>"
        f"<td>{escape(_date(entity.last_published_at))}</td>"
        f"<td>{links}</td></tr>"
    )


@router.get("/ui/political", response_class=HTMLResponse)
def ui_political(
    months: int = Query(default=0),
    date_from: str = Query(default="", max_length=10),
    date_to: str = Query(default="", max_length=10),
    hide_maybe_listed: bool = Query(default=False),
    page: int = Query(default=1, ge=1),
    db: Session = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    chosen = filters(months, date_from, date_to, hide_maybe_listed)
    found, total, maybe = _rows(db, chosen)
    on_page = _details(db, found[(page - 1) * PAGE_SIZE : page * PAGE_SIZE])
    rows = "".join(
        _html_row(position, row)
        for position, row in enumerate(on_page, start=(page - 1) * PAGE_SIZE + 1)
    )
    keep = chosen.query()
    # A period button clears the dates: the months are the choice then.
    periods = " ".join(
        f'<button type="submit" name="months" value="{key}" '
        f'class="chip{" active" if key == chosen.months and not chosen.custom else ""}" '
        "onclick=\"this.form.date_from.value='';this.form.date_to.value=''\">"
        f"{label}</button>"
        for key, label in PERIODS.items()
    )
    pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
    pager = " ".join(
        f'<a href="/ui/political?{urlencode({**keep, "page": number})}">'
        f"{'<b>' + str(number) + '</b>' if number == page else number}</a>"
        for number in range(1, pages + 1)
    )
    dates = (
        f'<label class="dates">с <input type="date" name="date_from" '
        f'value="{chosen.date_from.isoformat() if chosen.date_from else ""}"></label>'
        f'<label class="dates">по <input type="date" name="date_to" '
        f'value="{chosen.date_to.isoformat() if chosen.date_to else ""}"></label>'
        '<button type="submit">Показать</button>'
    )
    body = f"""<form method="get" action="/ui/political" class="toolbar">
  <span class="chips">{periods}</span>
  <span class="chips">{dates}</span>
  <div class="filter-row">
    <!-- Unticked, only the hidden «false» is sent; ticked, the box's «true» comes last. -->
    <input type="hidden" name="hide_maybe_listed" value="false">
    <label class="check" title="Совпали имя и фамилия с перечнем, отчества нет с одной из сторон">
      <input id="box-hide-maybe" type="checkbox" name="hide_maybe_listed" value="true"{
        " checked" if chosen.hide_maybe_listed else ""
    } onchange="this.form.submit()"> Скрыть возможных в перечне ({maybe})</label>
    <a class="secondary" href="/ui/political/export.xlsx?{urlencode(keep)}">Скачать Excel</a>
  </div>
</form>
<p class="muted">Найдено: {total}. Фигуранты уголовных дел, которых нет в перечне
Росфинмониторинга, и дело которых — политическое преследование. Жирная статья — из списка
политических; «Последняя новость» показывает, свежий ли случай.</p>
<table><thead><tr><th>№</th><th>Фамилия Имя</th><th>Регион</th><th>Статьи УК</th>
<th>Почему политическое</th><th>Мемориал</th><th>Первая новость</th><th>Последняя новость</th>
<th>Публикации</th></tr></thead><tbody>{rows}</tbody></table>
<p class="pager">{pager if pages > 1 else ""}</p>"""
    return _page(
        "Список",
        body,
        active="political",
        instruction=(
            "Люди, против которых заведены политические уголовные дела и которых нет в перечне "
            "Росфинмониторинга."
        ),
        next_action=(
            "Выберите период, чтобы увидеть свежие случаи; скачайте Excel. Список обновляют "
            "шаги 4–6 в «Управлении»."
        ),
        db=db,
    )


def political_xlsx(rows: list[ListRow]) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.title = "Список"
    sheet.append(
        [
            "№",
            "Фамилия Имя",
            "Регион",
            "Статьи УК",
            "Почему политическое",
            "Мемориал",
            "Первая новость",
            "Последняя новость",
            "Возможно в перечне",
            "Публикации",
        ]
    )
    for position, row in enumerate(rows, start=1):
        link = next(
            (url for _, url, _ in row.links if url.startswith(("http://", "https://"))), None
        )
        values: list[Any] = [
            position,
            display_name(row.entity.name),
            _regions_text(row.entity) or None,
            _article_text(row.articles) or None,
            _basis(row),
            row.memorial,
            row.first_published.replace(tzinfo=None) if row.first_published else None,
            row.entity.last_published_at.replace(tzinfo=None)
            if row.entity.last_published_at
            else None,
            "да" if row.maybe_listed else None,
            "\n".join(url for _, url, _ in row.links) or None,
        ]
        sheet.append(values)
        line = position + 1
        # Names, reasons and URLs come from scraped sources: never let «=» become a formula.
        for column in (2, 3, 4, 5, 6, 10):
            if sheet.cell(row=line, column=column).value is not None:
                sheet.cell(row=line, column=column).data_type = "s"
        for column in (7, 8):
            sheet.cell(row=line, column=column).number_format = "DD.MM.YYYY"
        if link is not None:
            sheet.cell(row=line, column=10).hyperlink = link
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


@router.get("/ui/political/export.xlsx")
def ui_political_export(
    months: int = Query(default=0),
    date_from: str = Query(default="", max_length=10),
    date_to: str = Query(default="", max_length=10),
    hide_maybe_listed: bool = Query(default=False),
    db: Session = Depends(get_db),  # noqa: B008
) -> Response:
    """Every row of the page's filters, not only one page."""
    found, _, _ = _rows(db, filters(months, date_from, date_to, hide_maybe_listed))
    return Response(
        political_xlsx(_details(db, found)),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="political-list.xlsx"'},
    )
