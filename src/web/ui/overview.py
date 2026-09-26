"""«Обзор», the home page: what is new — the political cases' new cases and sentences, the
unnamed figurants to identify — and what waits for the operator. The pipeline, its runs
and the sources' errors are on «Управление»."""

from __future__ import annotations

from datetime import datetime
from html import escape
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from db.orm_models import UnnamedFigurantRecord
from entities.news import NEW_CASE, SENTENCE
from entities.politics import POLITICAL
from entities.unnamed import Candidates, candidates
from web.dependencies import get_db
from web.ui.entities import display_name
from web.ui.layout import _page
from web.ui.management import recent_source_errors
from web.ui.unnamed import _facts
from web.ui.workload import workload

router = APIRouter()

LATEST = 8
UNNAMED = 5
QUOTE_LIMIT = 160

# A political case's latest news of one kind, the latest first.
_LATEST_NEWS = text(
    """
    SELECT g.key, g.name, n.published_at, n.reason
    FROM entity_group_news n
    JOIN entity_groups g ON g.id = n.group_id
    JOIN entity_group_politics p ON p.group_id = g.id AND p.verdict = :political
    WHERE n.kind = :kind
    ORDER BY n.published_at DESC NULLS LAST, g.key
    LIMIT :limit
    """
)
_NEWS_COUNTS = text(
    """
    SELECT n.kind, count(*)
    FROM entity_group_news n
    JOIN entity_group_politics p ON p.group_id = n.group_id AND p.verdict = :political
    GROUP BY n.kind
    """
)
# The unnamed nobody has identified or closed yet.
_OPEN = """NOT EXISTS (
    SELECT 1 FROM unnamed_identity_resolutions r
    WHERE r.figurant_key = unnamed_figurants.key
      AND r.resolution IN ('rf_entry', 'existing_person', 'supplied_name', 'insufficient'))"""


def _day(moment: datetime | None) -> str:
    return moment.astimezone().strftime("%d.%m") if moment else "—"


def _news_band(db: Session, kind: str, title: str, count: int, empty: str) -> str:
    rows = db.execute(_LATEST_NEWS, {"political": POLITICAL, "kind": kind, "limit": LATEST}).all()
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


def _found(found: Candidates, *, age_told: bool = True) -> str:
    """How many of the list may be this person — the ones worth looking at."""
    if not age_told:
        return "возраст не назван — искать в перечне не по чему"
    if found.likely:
        return f"вероятных в перечне: {found.likely}"
    if found.total:
        return f"того возраста в перечне {found.total} — примет мало"
    return "в перечне никого"


def _unnamed_band(db: Session, open_count: int) -> str:
    latest = db.scalars(
        select(UnnamedFigurantRecord)
        .where(text(_OPEN))
        .order_by(UnnamedFigurantRecord.published_at.desc().nulls_last(), UnnamedFigurantRecord.id)
        .limit(UNNAMED)
    ).all()
    items = []
    for figurant in latest:
        found = candidates(db, figurant)
        quote_text = " ".join(figurant.quote.split())
        if len(quote_text) > QUOTE_LIMIT:
            quote_text = quote_text[:QUOTE_LIMIT].rstrip() + "…"
        items.append(
            f'<li><span class="when">{_day(figurant.published_at)}</span> '
            f"«{escape(quote_text)}»"
            f'<span class="why">{_facts(figurant)} · {_found(found, age_told=figurant.age is not None)}</span></li>'
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
    counts = {
        str(kind): int(count)
        for kind, count in db.execute(_NEWS_COUNTS, {"political": POLITICAL}).all()
    }
    new_cases, sentences = counts.get(NEW_CASE, 0), counts.get(SENTENCE, 0)
    decisions = work.pairs + work.unclear_roles + work.unclear_verdicts
    queue = (
        f"""<p class="queue-line">Нужно ваше решение:
  <a href="/ui/queue#pairs">спорных совпадений — {work.pairs}</a> ·
  <a href="/ui/queue#roles">неясных ролей — {work.unclear_roles}</a> ·
  <a href="/ui/queue#verdicts">неясной политичности — {work.unclear_verdicts}</a>
  <a class="button-link" href="/ui/queue">Разобрать</a></p>"""
        if decisions
        else '<p class="queue-line muted">Решений оператора не ждёт ничего.</p>'
    )
    failed = recent_source_errors(db)
    errors = (
        f'<p class="warning">Источников с ошибками загрузки за неделю: {failed} — '
        '<a href="/ui/management#source-errors">Управление</a>.</p>'
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
        next_action="Откройте человека или «Все →»; спорное разберите в очереди.",
        db=db,
    )
