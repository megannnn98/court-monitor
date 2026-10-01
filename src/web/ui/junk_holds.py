"""«Отсев: на проверке»: the articles the junk screen held back from the purge.

Each one: the publication, why it was held (the screen's score and cutoff — no proof of a
case), the events today's extraction found, and a person's two ways out: «Мусор» (the
next purge deletes it) or «Извлечь заново» (after the extraction was fixed; an event found
returns the article to the pipeline). `monitoring.junk_holds` does the work.
"""

from __future__ import annotations

from datetime import datetime
from html import escape
from typing import Any
from urllib.parse import parse_qs, urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from monitoring.junk_holds import hold_again, mark_junk, reextract
from monitoring.junk_screen import HELD, JUNK
from web.dependencies import get_db, session_factory_for
from web.ui.layout import _page, pager

router = APIRouter()

PAGE_SIZE = 30
_STATUSES = {HELD: "На проверке", JUNK: "Отмечены как мусор"}

_HOLDS = text(
    """
    SELECT h.article_id, h.status, h.score, h.cutoff, h.screen, h.reason, h.note,
           h.created_at, a.title, a.published_at, s.name AS source, d.canonical_url,
           left(a.text, 400) AS start,
           (SELECT string_agg(DISTINCT e.event_type, ', ') FROM extracted_events e
            WHERE e.extraction_run_id = (
                SELECT r.id FROM article_extraction_runs r
                WHERE r.article_id = a.id AND r.status = 'succeeded'
                ORDER BY r.id DESC LIMIT 1)) AS events
    FROM junk_screen_holds h
    JOIN parsed_articles a ON a.id = h.article_id
    JOIN source_documents d ON d.id = a.document_id
    JOIN sources s ON s.id = d.source_id
    WHERE h.status = :status
    ORDER BY h.score DESC, h.article_id
    """
)
# The same news told twice. Sources copy one another, and reading one story five times is
# the operator's dullest work — so held articles that tell one story are shown together
# and can be dismissed together. Two signs, each on its own enough:
# - they name the same person (first name and surname, as the extraction normalised it);
# - their titles are nearly the same text (trigram similarity, pg_trgm).
# Neither proves it is one story, so nothing is merged or deleted on its own: the cards
# only stand together, and every one keeps its own buttons.
TITLE_SIMILARITY = 0.5
_SAME_PERSON = text(
    """
    WITH latest AS (
        SELECT DISTINCT ON (r.article_id) r.article_id, r.id
        FROM article_extraction_runs r
        JOIN junk_screen_holds h ON h.article_id = r.article_id AND h.status = :status
        WHERE r.status = 'succeeded'
        ORDER BY r.article_id, r.id DESC
    ), named AS (
        SELECT DISTINCT l.article_id, lower(m.normalized_text) AS name
        FROM latest l JOIN entity_mentions m ON m.extraction_run_id = l.id
        WHERE m.entity_type = 'person' AND m.normalized_text LIKE '% %'
    )
    SELECT x.article_id, y.article_id
    FROM named x JOIN named y ON y.name = x.name AND x.article_id < y.article_id
    """
)
_SAME_TITLE = text(
    """
    WITH held AS (
        SELECT a.id, left(a.title, 300) AS title
        FROM junk_screen_holds h JOIN parsed_articles a ON a.id = h.article_id
        WHERE h.status = :status AND a.title IS NOT NULL AND length(a.title) >= 20
    )
    SELECT x.id, y.id FROM held x JOIN held y ON x.id < y.id
    WHERE similarity(x.title, y.title) >= :similarity
    """
)
_COUNTS = text("SELECT status, count(*) FROM junk_screen_holds GROUP BY status")


def same_news(ids: list[int], pairs: list[tuple[int, int]]) -> list[list[int]]:
    """The articles in groups of one story, in the order of `ids`.

    A pair joins two articles; pairs chain (A with B, B with C: one story of three). A
    group stands where its first article stood, so the list keeps its order.
    """
    parent = {article: article for article in ids}

    def root(article: int) -> int:
        while parent[article] != article:
            parent[article] = parent[parent[article]]
            article = parent[article]
        return article

    for first, second in pairs:
        if first in parent and second in parent:
            parent[root(second)] = root(first)
    groups: dict[int, list[int]] = {}
    for article in ids:
        groups.setdefault(root(article), []).append(article)
    return list(groups.values())


def _day(moment: datetime | None) -> str:
    return moment.astimezone().strftime("%d.%m.%Y") if moment else "—"


def _button(action: str, article_id: int, label: str, status: str, page: int) -> str:
    return (
        f'<form method="post" action="/ui/junk-holds/{action}" class="inline-form">'
        f'<input type="hidden" name="article" value="{article_id}">'
        f'<input type="hidden" name="back" value="{escape(urlencode({"status": status, "page": page}), quote=True)}">'
        f'<button type="submit" class="secondary">{escape(label)}</button></form>'
    )


def _card(row: Any, status: str, page: int) -> str:
    article_id = row.article_id
    actions = (
        _button("reextract", article_id, "Извлечь заново", status, page)
        + _button("junk", article_id, "Мусор", status, page)
        if row.status == HELD
        else _button("hold", article_id, "Вернуть на проверку", status, page)
    )
    note = row.note
    return f"""<article class="band" id="a-{article_id}">
  <h3><a href="/ui/articles/{article_id}">{escape(row.title or "Без заголовка")}</a></h3>
  <p class="muted">{_day(row.published_at)} · {escape(row.source)} ·
  <a href="{escape(row.canonical_url, quote=True)}" rel="noreferrer">источник</a> ·
  оценка {row.score:.2f} (порог {row.cutoff:.2f}) ·
  события извлечения: {escape(row.events or "нет")}</p>
  <p>{escape(" ".join((row.start or "").split()))}…</p>
  <p class="muted">{escape(row.reason)}</p>
  {f'<p class="warning">{escape(note)}</p>' if note else ""}
  <div class="actions">{actions}</div>
</article>"""


def _pages(stories: list[list[int]]) -> list[list[list[int]]]:
    """The stories in pages of about `PAGE_SIZE` articles. A story is never cut by a page
    break: a page takes whole stories until it is full."""
    pages: list[list[list[int]]] = []
    count = PAGE_SIZE
    for story in stories:
        if count >= PAGE_SIZE:
            pages.append([])
            count = 0
        pages[-1].append(story)
        count += len(story)
    return pages


def _story(group: list[int], by_id: dict[int, Any], status: str, page: int) -> str:
    """Several articles that tell one story: together, with one button for them all."""
    everything = (
        '<form method="post" action="/ui/junk-holds/junk-all" class="inline-form">'
        f'<input type="hidden" name="articles" value="{",".join(map(str, group))}">'
        f'<input type="hidden" name="back" value="{escape(urlencode({"status": status, "page": page}), quote=True)}">'
        f'<button type="submit" class="secondary">Мусор — все {len(group)}</button></form>'
        if status == HELD
        else ""
    )
    return (
        '<section class="same-news"><p class="same-news-head"><strong>Похоже на одну новость: '
        f"{len(group)} публикации.</strong> Совпал человек или заголовок — проверьте, "
        f"прежде чем убирать все. {everything}</p>"
        + "".join(_card(by_id[article], status, page) for article in group)
        + "</section>"
    )


@router.get("/ui/junk-holds", response_class=HTMLResponse)
def ui_junk_holds(
    status: str = Query(default=HELD, pattern=f"^({HELD}|{JUNK})$"),
    page: int = Query(default=1, ge=1),
    released: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    counts = {str(key): int(value) for key, value in db.execute(_COUNTS).all()}
    rows = db.execute(_HOLDS, {"status": status}).all()
    by_id = {row.article_id: row for row in rows}
    pairs = [
        (first, second)
        for first, second in [
            *db.execute(_SAME_PERSON, {"status": status}).all(),
            *db.execute(_SAME_TITLE, {"status": status, "similarity": TITLE_SIMILARITY}).all(),
        ]
    ]
    pages = _pages(same_news(list(by_id), pairs))
    cards = [
        _card(by_id[story[0]], status, page)
        if len(story) == 1
        else _story(story, by_id, status, page)
        for story in (pages[page - 1] if page <= len(pages) else [])
    ]
    chips = " ".join(
        f'<a class="chip{" active" if key == status else ""}" '
        f'href="/ui/junk-holds?{urlencode({"status": key})}">{label} ({counts.get(key, 0)})</a>'
        for key, label in _STATUSES.items()
    )
    empty = (
        '<p class="empty">Ничего не удержано: отсев выключен или ничего не заподозрил.</p>'
        if status == HELD
        else '<p class="empty">Никто ничего не отметил как мусор.</p>'
    )
    notice = (
        f'<p class="notice">Статья <a href="/ui/articles/{released}">#{released}</a>: найдено '
        "уголовное событие, она возвращена в работу — её возьмёт следующая сборка людей.</p>"
        if released
        else ""
    )
    body = f"""<p><a href="/ui/cycle">Назад к циклу</a></p>
{notice}<p class="chips">{chips}</p>
<p class="muted">Статьи, в которых извлечение не нашло уголовного события, но модель отсева
сочла их похожими на новость об уголовном деле. Они не удалены и дальше по конвейеру не идут:
события в них нет. Высокая оценка — не доказательство дела. Если дело есть, исправьте правила
извлечения и нажмите «Извлечь заново»: найденное событие вернёт статью в работу. Если нет —
«Мусор»: статья удалится при следующей очистке.</p>
{"".join(cards) or empty}
{pager("/ui/junk-holds", {"status": status}, page, len(pages))}"""
    return _page(
        "Отсев: на проверке",
        body,
        active="queue",
        instruction="Статьи, которые очистка удалила бы, а модель отсева удержала.",
        db=db,
    )


async def _form(request: Request) -> dict[str, str]:
    return {
        key: values[0]
        for key, values in parse_qs(
            (await request.body()).decode("utf-8", errors="replace")
        ).items()
    }


def _article(form: dict[str, str]) -> int:
    value = form.get("article", "")
    if not value.isdigit():
        raise HTTPException(status_code=400, detail="Не указана статья")
    return int(value)


def _back(form: dict[str, str], article_id: int) -> str:
    kept = parse_qs(form.get("back", ""))
    status = kept.get("status", [HELD])[0]
    page = kept.get("page", ["1"])[0]
    params = {
        "status": status if status in _STATUSES else HELD,
        "page": page if page.isdigit() else "1",
    }
    return f"/ui/junk-holds?{urlencode(params)}#a-{article_id}"


@router.post("/ui/junk-holds/junk", response_model=None)
async def ui_junk_holds_junk(
    request: Request,
    db: Session = Depends(get_db),  # noqa: B008
) -> RedirectResponse:
    form = await _form(request)
    article_id = _article(form)
    if not mark_junk(db, article_id):
        raise HTTPException(status_code=404, detail="Статья не на проверке")
    db.commit()
    return RedirectResponse(_back(form, article_id), status_code=303)


@router.post("/ui/junk-holds/junk-all", response_model=None)
async def ui_junk_holds_junk_all(
    request: Request,
    db: Session = Depends(get_db),  # noqa: B008
) -> RedirectResponse:
    """«Мусор» for every article of one story. All of them, or none: one that is no
    longer held means the page is stale, and the operator should look again."""
    form = await _form(request)
    values = form.get("articles", "").split(",")
    if not values or not all(value.isdigit() for value in values):
        raise HTTPException(status_code=400, detail="Не указаны статьи")
    articles = [int(value) for value in values]
    if not all(mark_junk(db, article_id) for article_id in articles):
        db.rollback()
        raise HTTPException(status_code=404, detail="Статья не на проверке")
    db.commit()
    return RedirectResponse(_back(form, articles[0]), status_code=303)


@router.post("/ui/junk-holds/hold", response_model=None)
async def ui_junk_holds_hold(
    request: Request,
    db: Session = Depends(get_db),  # noqa: B008
) -> RedirectResponse:
    form = await _form(request)
    article_id = _article(form)
    if not hold_again(db, article_id):
        raise HTTPException(status_code=404, detail="Статья не отмечена как мусор")
    db.commit()
    return RedirectResponse(_back(form, article_id), status_code=303)


@router.post("/ui/junk-holds/reextract", response_model=None)
async def ui_junk_holds_reextract(
    request: Request,
    db: Session = Depends(get_db),  # noqa: B008
) -> RedirectResponse:
    form = await _form(request)
    article_id = _article(form)
    held = db.scalar(
        text("SELECT status FROM junk_screen_holds WHERE article_id = :article"),
        {"article": article_id},
    )
    if held != HELD:
        raise HTTPException(status_code=404, detail="Статья не на проверке")
    outcome = reextract(session_factory_for(db), article_id)
    location = _back(form, article_id)
    if outcome.released:
        path = location.partition("#")[0]
        location = f"{path}&{urlencode({'released': article_id})}"
    return RedirectResponse(location, status_code=303)
