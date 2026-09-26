"""Operator console: the person card and the article view behind a candidate."""

from html import escape
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from web.candidate_rows import _surname_first
from web.dependencies import get_db
from web.routers.articles import _article_response
from web.routers.persons import get_person_detail
from web.ui.entities import _EVENT_LABELS, display_name
from web.ui.layout import _fmt, _page, external_url
from web.ui.publications import _EVENTS, _PEOPLE

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
        active="candidates",
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
    people = db.execute(_PEOPLE, {"articles": [article_id], "context": 0}).all()
    events = db.execute(_EVENTS, {"articles": [article_id]}).all()
    people_html = ", ".join(
        f'<a href="/ui/investigations/{quote(key)}">{escape(display_name(name))}</a>'
        for _article, key, name, _mentions in sorted(people, key=lambda row: -row[3])
    )
    events_html = " ".join(
        f'<span class="badge">{escape(_EVENT_LABELS.get(kind, kind))}'
        f"{f': {count}' if count > 1 else ''}</span>"
        for _article, kind, count in sorted(events, key=lambda row: row[1])
    )
    url = external_url(article.url)
    source = (
        f'<a href="{escape(url, quote=True)}" rel="noopener noreferrer" target="_blank">'
        f"{escape(url)}</a>"
        if url
        else escape(article.url)
    )
    return _page(
        article.title,
        f"""<p><a href="/ui/publications">← Публикации</a> · {source}</p>
<section class="band">
  <dl class="facts">
    <dt>Люди</dt><dd>{people_html or '<span class="muted">—</span>'}</dd>
    <dt>События</dt><dd>{events_html or '<span class="muted">—</span>'}</dd>
  </dl>
</section>
<article>{rendered}</article>""",
        active="publications",
        instruction="Полный текст публикации — первоисточник доказательств.",
        next_action="Проверьте подсвеченный фрагмент; откройте досье упомянутого человека.",
        db=db,
    )
