"""«Публикации»: the publications with a criminal case, searched by their title and text,
filtered by source; each with the people it names and the events found in it. A page is
three queries whatever its size: the rows, their people, their events."""

from __future__ import annotations

from html import escape
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from web.dependencies import get_db
from web.ui.layout import _page, pager
from web.views.entities import EVENT_LABELS, display_name
from web.views.publications import PAGE_SIZE, PEOPLE_SHOWN, read_publications

router = APIRouter()


@router.get("/ui/publications", response_class=HTMLResponse)
def ui_publications(
    q: str = Query(default="", max_length=200),
    source: int = Query(default=0, ge=0),
    page: int = Query(default=1, ge=1),
    db: Session = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    data = read_publications(db, q=q, source=source, page=page)
    total, rows, people, events, sources = (
        data.total,
        data.rows,
        data.people,
        data.events,
        data.sources,
    )
    options = "".join(
        f'<option value="{source_id}"{" selected" if source_id == source else ""}>'
        f"{escape(name)} ({count})</option>"
        for source_id, name, count in sources
    )

    def people_of(article_id: int) -> str:
        found = sorted(people.get(article_id, []), key=lambda item: -item[2])
        links = ", ".join(
            f'<a href="/ui/investigations/{quote(key)}">{escape(display_name(name))}</a>'
            for key, name, _mentions in found[:PEOPLE_SHOWN]
        )
        more = (
            f' <span class="muted">и ещё {len(found) - PEOPLE_SHOWN}</span>'
            if len(found) > PEOPLE_SHOWN
            else ""
        )
        return (links + more) or '<span class="muted">—</span>'

    def events_of(article_id: int) -> str:
        return (
            " ".join(
                f'<span class="badge">{escape(EVENT_LABELS.get(kind, kind))}'
                f"{f': {count}' if count > 1 else ''}</span>"
                for kind, count in sorted(events.get(article_id, []))
            )
            or '<span class="muted">—</span>'
        )

    body_rows = "".join(
        (
            f"<tr><td>{row.published_at.astimezone():%d.%m.%Y}</td>"
            if row.published_at
            else "<tr><td>—</td>"
        )
        + f'<td><a href="/ui/articles/{row.id}">{escape(row.title)}</a>'
        f'<br><span class="muted">{escape(row.source)}</span></td>'
        f"<td>{people_of(row.id)}</td><td>{events_of(row.id)}</td></tr>"
        for row in rows
    )
    pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
    table = (
        f"""<table class="sticky-head"><caption class="visually-hidden">Публикации</caption>
<thead><tr><th scope="col">Дата</th><th scope="col">Публикация</th><th scope="col">Люди</th>
<th scope="col">События</th></tr></thead><tbody>{body_rows}</tbody></table>"""
        if body_rows
        else '<p class="empty">Ничего не найдено.</p>'
    )
    body = f"""<form method="get" class="toolbar" role="search">
  <label for="pub-search" class="visually-hidden">Слова из заголовка или текста</label>
  <input id="pub-search" type="search" name="q" value="{escape(q, quote=True)}"
    placeholder="Слова из заголовка или текста">
  <label for="pub-source" class="visually-hidden">Источник</label>
  <select id="pub-source" name="source">
    <option value="0">Все источники</option>{options}
  </select>
  <button type="submit">Найти</button>
</form>
<p class="muted">Найдено: {total}.</p>
<section class="band table-band">{table}</section>
{pager("/ui/publications", {"q": q.strip(), "source": str(source)}, page, pages)}"""
    return _page(
        "Публикации",
        body,
        active="publications",
        instruction="Публикации с уголовными делами: источник, люди и события каждой.",
        db=db,
    )
