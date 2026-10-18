"""«Публикации»: the publications with a criminal case, searched by their title and text,
filtered by source; each with the people it names and the events found in it. A page is
three queries whatever its size: the rows, their people, their events."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from html import escape
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from application import group_followers
from ingestion import read as articles
from processing import read as identity
from processing.evidence.publications import person_evidence
from web.dependencies import get_db
from web.ui.entities import EVENT_LABELS, display_name
from web.ui.layout import _page, pager

router = APIRouter()

PAGE_SIZE = 50
PEOPLE_SHOWN = 8


def people_and_events(
    db: Session, article_ids: Sequence[int]
) -> tuple[dict[int, list[tuple[str, str, int]]], dict[int, list[tuple[str, int]]]]:
    """Per article, the people it names (key, name, mentions) and its events (type, count)."""
    people: dict[int, list[tuple[str, str, int]]] = defaultdict(list)
    events: dict[int, list[tuple[str, int]]] = defaultdict(list)
    if article_ids:
        pairs = sorted(
            {
                (item.article_id, item.group_id)
                for item in person_evidence(
                    db, group_followers(), articles=article_ids, quotes=False
                )
            }
        )
        groups = identity.groups(db, {group_id for _, group_id in pairs})
        for article_id, group_id in pairs:
            # A rebuild committed between the two reads may have taken the group away.
            if (group := groups.get(group_id)) is not None:
                people[article_id].append((group.key, group.name, group.mention_count))
        for article_id, event_type, count in identity.event_counts(db, article_ids):
            events[article_id].append((event_type, count))
    return people, events


@dataclass(frozen=True)
class PublicationPage:
    """One page of «Публикации»; read by the legacy page and `GET /api/v1/publications`."""

    total: int
    rows: Sequence[articles.ListedArticle]
    people: dict[int, list[tuple[str, str, int]]]
    events: dict[int, list[tuple[str, int]]]
    # Every source with its count of publications, for the filter.
    sources: Sequence[tuple[int, str, int]]


def read_publications(db: Session, *, q: str, source: int, page: int) -> PublicationPage:
    total, rows = articles.browse(
        db, q=q.strip(), source_id=source, limit=PAGE_SIZE, offset=(page - 1) * PAGE_SIZE
    )
    people, events = people_and_events(db, [row.id for row in rows])
    return PublicationPage(total, rows, people, events, articles.source_counts(db))


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
