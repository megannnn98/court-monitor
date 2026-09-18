"""Operator console: person card, article view, search."""

from html import escape

from fastapi import APIRouter, Depends, Query
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from sources.models import SearchHit
from web.candidate_rows import _surname_first
from web.dependencies import get_db
from web.routers.articles import _article_response
from web.routers.persons import get_person_detail
from web.routers.search import search_articles
from web.ui.layout import _fmt, _page

router = APIRouter()


@router.get("/ui/persons/{person_id}")
def ui_get_person(
    person_id: int,
    db: Session = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    detail = get_person_detail(person_id, db)
    aliases = "".join(
        f"<li>{escape(alias.surface_text)} <span>{escape(alias.origin)}</span></li>"
        for alias in detail.aliases
    )
    events = "".join(
        f"""<tr>
  <td>{event.id}</td>
  <td>{escape(event.event_type)}</td>
  <td>{_fmt(event.event_date)}</td>
  <td>{escape(event.role)}</td>
  <td><a href="/ui/articles/{event.evidence.article_id}?start={event.evidence.start_offset}&end={event.evidence.end_offset}">{escape(event.evidence.title)}</a></td>
  <td>{escape(event.evidence.text)}</td>
</tr>"""
        for event in detail.events
    )
    persecution = detail.persecution.status if detail.persecution else "—"
    rosfin = detail.rosfinmonitoring.status if detail.rosfinmonitoring else "—"
    return _page(
        _surname_first(detail.person.canonical_name),
        f"""<section class="band">
  <dl>
    <dt>ID</dt><dd>{detail.person.id}</dd>
    <dt>Статус</dt><dd>{escape(detail.person.status)}</dd>
    <dt>Persecution</dt><dd>{escape(persecution)}</dd>
    <dt>Росфинмониторинг</dt><dd>{escape(rosfin)}</dd>
  </dl>
</section>
<h2>Алиасы</h2>
<ul>{aliases}</ul>
<h2>События</h2>
<table>
  <thead><tr><th>ID</th><th>Тип</th><th>Дата</th><th>Роль</th><th>Статья</th><th>Span</th></tr></thead>
  <tbody>{events}</tbody>
</table>""",
        active="search",
        instruction="Карточка Person показывает только проверяемые факты с переходом к source span.",
        next_action="Откройте статью в строке события и проверьте подсвеченный evidence span.",
        db=db,
    )


@router.get("/ui/articles/{article_id}")
def ui_get_article(
    article_id: int,
    start: int | None = Query(default=None, ge=0),
    end: int | None = Query(default=None, ge=0),
    db: Session = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    article = _article_response(db, article_id)
    text = article.text
    if start is not None and end is not None and start <= end <= len(text):
        rendered = (
            escape(text[:start]) + f"<mark>{escape(text[start:end])}</mark>" + escape(text[end:])
        )
    else:
        rendered = escape(text)
    return _page(
        article.title,
        f"""<p><a href="{escape(article.url)}">{escape(article.url)}</a></p>
<article>{rendered}</article>""",
        active="search",
        instruction="Это полный ParsedArticle.text — source of truth для evidence.",
        next_action="Проверьте подсвеченный span или вернитесь к карточке Person.",
        db=db,
    )


@router.get("/ui/search")
def ui_search(
    query: str | None = Query(default=None, max_length=500),
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    results: list[SearchHit] = []
    if query and query.strip():
        results = search_articles(query, limit, db)
    rows = "\n".join(
        f"""<tr>
  <td><a href="/ui/articles/{hit.article_id}">{escape(hit.title)}</a></td>
  <td>{_fmt(hit.published_at)}</td>
  <td>{_fmt(hit.score)}</td>
</tr>"""
        for hit in results
    )

    return _page(
        "Поиск",
        f"""<form method="get" class="search">
  <input name="query" value="{escape(query or "")}" autofocus>
  <button>Искать</button>
</form>
<table><thead><tr><th>Статья</th><th>Дата</th><th>Score</th></tr></thead><tbody>{rows}</tbody></table>""",
        active="search",
        instruction="Lexical search ищет по ParsedArticle.text через PostgreSQL russian tsvector.",
        next_action="Введите фразу, откройте статью и используйте её как provenance, не как финальный результат.",
        db=db,
    )
