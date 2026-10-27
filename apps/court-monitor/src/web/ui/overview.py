"""«Обзор», the home page: what is new — the political cases' new cases and sentences, the
unnamed figurants to identify — and what waits for the operator. The pipeline, its runs
and the sources' errors are on «Управление»."""

from __future__ import annotations

from html import escape
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from analysis.news.case_news import NEW_CASE, SENTENCE
from analysis.unnamed.matching import candidates
from web.dependencies import get_db
from web.ui.layout import _page
from web.views.entities import display_name
from web.views.management import recent_source_errors
from web.views.overview import _day, _found, latest_news, news_counts, open_unnamed, short_quote
from web.views.unnamed import facts_text
from web.views.workload import workload

router = APIRouter()


def _news_band(db: Session, kind: str, title: str, count: int, empty: str) -> str:
    rows = latest_news(db, kind)
    items = "".join(
        f'<li><span class="when">{_day(published_at)}</span> '
        f'<a href="/ui/investigations/{quote(key)}">{escape(display_name(name))}</a>'
        f'<span class="why">{escape(reason)}</span></li>'
        for key, name, published_at, reason in rows
    )
    more = f"/ui/political?{urlencode({'months': 0, 'news': kind})}"
    return f"""<section class="band news-band" aria-labelledby="{kind}-title">
  <h2 id="{kind}-title">{title} <span class="count">{count}</span></h2>
  {f'<ul class="news-list">{items}</ul>' if items else f'<p class="empty">{empty}</p>'}
  {f'<p><a href="{more}">Все: {count} →</a></p>' if count else ""}
</section>"""


def _unnamed_band(db: Session, open_count: int) -> str:
    items = []
    for figurant in open_unnamed(db):
        found = candidates(db, figurant)
        quote_text = short_quote(figurant.quote)
        items.append(
            f'<li><span class="when">{_day(figurant.published_at)}</span> '
            f"«{escape(quote_text)}»"
            f'<span class="why">{escape(facts_text(figurant))} · {_found(found, age_told=figurant.age is not None)}</span></li>'
        )
    return f"""<section class="band" aria-labelledby="unnamed-title">
  <h2 id="unnamed-title">Неопознанные фигуранты <span class="count">{open_count}</span></h2>
  <p class="muted">Публикация не называет человека («17-летний житель Тюмени»); кто это может
  быть — по перечню Росфинмониторинга.</p>
  {
        f'<ul class="news-list">{"".join(items)}</ul>'
        if items
        else '<p class="empty">Неопознанных нет.</p>'
    }
  {f'<p><a href="/ui/unnamed">Все: {open_count} →</a></p>' if open_count else ""}
</section>"""


@router.get("/ui/overview", response_class=HTMLResponse)
def ui_overview(db: Session = Depends(get_db)) -> HTMLResponse:  # noqa: B008
    work = workload(db)
    counts = news_counts(db)
    new_cases, sentences = counts.get(NEW_CASE, 0), counts.get(SENTENCE, 0)
    decisions = work.pairs + work.unclear_roles + work.unclear_verdicts
    queue = (
        f"""<p class="queue-line">Нужно ваше решение:
  <a href="/ui/pairs">спорных совпадений — {work.pairs}</a> ·
  <a href="/ui/roles">неясных ролей — {work.unclear_roles}</a> ·
  <a href="/ui/politics-review">неясной политичности — {work.unclear_verdicts}</a>
  <a class="button-link" href="/ui/cycle">К циклу</a></p>"""
        if decisions
        else '<p class="queue-line muted">Решений оператора не ждёт ничего.</p>'
    )
    failed = recent_source_errors(db)
    errors = (
        f'<p class="warning">Источников с ошибками загрузки за неделю: {failed} — '
        '<a href="/ui/runs#source-errors">Журнал запусков</a>.</p>'
        if failed
        else ""
    )
    body = f"""{errors}
<div class="overview-grid news-grid">
{_news_band(db, NEW_CASE, "Новые дела", new_cases, "Новых дел нет.")}
{_news_band(db, SENTENCE, "Приговоры", sentences, "Приговоров нет.")}
</div>
{_unnamed_band(db, work.unnamed)}
{queue}"""
    return _page(
        "Обзор",
        body,
        active="overview",
        instruction=(
            "Что нового: политические дела, о которых свежая новость — новое дело или "
            "приговор, и фигуранты, которых публикации не называют."
        ),
        db=db,
    )
