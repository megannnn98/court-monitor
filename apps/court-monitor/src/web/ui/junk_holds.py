"""«Отсев: на проверке»: the articles the junk screen held back from the purge.

Each one: the publication, why it was held (the screen's score and cutoff — no proof of a
case), the events today's extraction found, and a person's two ways out: «Мусор» (the
next purge deletes it) or «Извлечь заново» (after the extraction was fixed; an event found
returns the article to the pipeline). `processing.junk.holds` does the work.
"""

from __future__ import annotations

from html import escape
from typing import Any
from urllib.parse import parse_qs, urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from processing.junk.hold_reader import RELEASED
from processing.junk.hold_view import MODEL_JUNK_VIEW
from processing.junk.screen import HELD, JUNK
from web.dependencies import get_db
from web.ui.layout import _page, pager
from web.views.junk_holds import (
    _NOTE_LABELS,
    _STATUSES,
    _day,
    apply_hold,
    apply_junk,
    apply_junk_all,
    apply_reextract,
    apply_release,
    apply_unrelease,
    default_status,
    held_stories,
    junk_counts,
)

router = APIRouter()


def _button(action: str, article_id: int, label: str, status: str, page: int) -> str:
    return (
        f'<form method="post" action="/ui/junk-holds/{action}" class="inline-form">'
        f'<input type="hidden" name="article" value="{article_id}">'
        f'<input type="hidden" name="back" value="{escape(urlencode({"status": status, "page": page}), quote=True)}">'
        f'<button type="submit" class="secondary">{escape(label)}</button></form>'
    )


def _card(row: Any, status: str, page: int) -> str:
    article_id = row.article_id
    if row.status == HELD:
        actions = (
            _button("release", article_id, "Это дело — в работу", status, page)
            + _button("reextract", article_id, "Извлечь заново", status, page)
            + _button("junk", article_id, "Мусор", status, page)
        )
    elif row.status == RELEASED:
        # The model's mistake, undone: the article waits for the purge again. Its own
        # address: «Мусор» pressed on a page opened before a release must not undo it.
        actions = _button("unrelease", article_id, "Мусор", status, page)
    else:
        actions = _button("hold", article_id, "Вернуть на проверку", status, page)
    note = row.note
    return f"""<article class="band" id="a-{article_id}">
  <h3><a href="/ui/articles/{article_id}">{escape(row.title or "Без заголовка")}</a></h3>
  <p class="muted">{_day(row.published_at)} · {escape(row.source)} ·
  <a href="{escape(row.canonical_url, quote=True)}" rel="noreferrer">источник</a> ·
  оценка {row.score:.2f} (порог {row.cutoff:.2f}) ·
  события извлечения: {escape(row.events or "нет")}</p>
  <p>{escape(" ".join((row.start or "").split()))}…</p>
  <p class="muted">{escape(row.reason)}</p>
  {f'<p class="warning">{_NOTE_LABELS.get(row.reader_verdict, "")}{escape(note)}</p>' if note else ""}
  <div class="actions">{actions}</div>
</article>"""


def _story(group: list[int], by_id: dict[int, Any], status: str, page: int) -> str:
    """Several articles that tell one story: together, with one button for them all."""
    everything = (
        '<form method="post" action="/ui/junk-holds/junk-all" class="inline-form">'
        f'<input type="hidden" name="articles" value="{",".join(map(str, group))}">'
        f'<input type="hidden" name="back" value="{escape(urlencode({"status": status, "page": page}), quote=True)}">'
        f'<button type="submit" class="secondary">Мусор — все {len(group)}</button></form>'
        if status in (HELD, MODEL_JUNK_VIEW)
        else ""
    )
    return (
        '<section class="same-news"><p class="same-news-head"><strong>Похоже на одну новость, публикаций: '
        f"{len(group)}.</strong> Совпал человек или заголовок — проверьте, "
        f"прежде чем убирать все. {everything}</p>"
        + "".join(_card(by_id[article], status, page) for article in group)
        + "</section>"
    )


@router.get("/ui/junk-holds", response_class=HTMLResponse)
def ui_junk_holds(
    status: str | None = Query(default=None, pattern=f"^({'|'.join(_STATUSES)})$"),
    page: int = Query(default=1, ge=1),
    released: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    counts = junk_counts(db)
    if status is None:
        status = default_status(counts)
    by_id, pages = held_stories(db, status)
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
    empty = {
        HELD: '<p class="empty">Ничего не ждёт вашего решения.</p>',
        MODEL_JUNK_VIEW: '<p class="empty">Модель ничего не сочла мусором.</p>',
        RELEASED: '<p class="empty">Модель ничего не выпустила в работу.</p>',
        JUNK: '<p class="empty">Никто ничего не отметил как мусор.</p>',
    }[status]
    # Everything the model called junk goes by one press, once a person has looked.
    everything = (
        '<form method="post" action="/ui/junk-holds/junk-all" class="inline-form">'
        f'<input type="hidden" name="articles" value="{",".join(map(str, by_id))}">'
        f'<input type="hidden" name="back" value="{escape(urlencode({"status": status}), quote=True)}">'
        f'<button type="submit">Мусор — все {len(by_id)}</button></form>'
        if status == MODEL_JUNK_VIEW and by_id
        else ""
    )
    notice = (
        f'<p class="notice">Статья <a href="/ui/articles/{released}">#{released}</a>: найдено '
        "уголовное событие, она возвращена в работу — её возьмёт следующая сборка людей.</p>"
        if released
        else ""
    )
    body = f"""<p><a href="/ui/cycle">Назад к циклу</a></p>
{notice}<p class="chips">{chips}</p>
<p class="muted">Статьи, в которых правила извлечения не нашли уголовного события, но отсев
счёл их похожими на новость об уголовном деле. Каждую целиком читает модель. Где она видит
уголовное дело — политическое или с неясным мотивом, — статья уходит в работу сама: список
«Выпущено в работу». Обычную уголовщину и то, что делом не является, модель не удаляет, а
складывает в «Модель считает мусором»: просмотрите причины и уберите всё одной кнопкой.
«На проверке» остаётся только то, по чему модель не ответила.</p>
<p class="muted">«Это дело — в работу» берёт статью в работу вопреки модели; «Мусор» у
выпущенной статьи возвращает её под очистку. Статья удаляется при следующей очистке только
после слова человека.</p>
{f'<p class="actions">{everything}</p>' if everything else ""}
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
    apply_junk(db, article_id)
    return RedirectResponse(_back(form, article_id), status_code=303)


@router.post("/ui/junk-holds/unrelease", response_model=None)
async def ui_junk_holds_unrelease(
    request: Request,
    db: Session = Depends(get_db),  # noqa: B008
) -> RedirectResponse:
    form = await _form(request)
    article_id = _article(form)
    apply_unrelease(db, article_id)
    return RedirectResponse(_back(form, article_id), status_code=303)


@router.post("/ui/junk-holds/release", response_model=None)
async def ui_junk_holds_release(
    request: Request,
    db: Session = Depends(get_db),  # noqa: B008
) -> RedirectResponse:
    form = await _form(request)
    article_id = _article(form)
    apply_release(db, article_id)
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
    apply_junk_all(db, articles)
    return RedirectResponse(_back(form, articles[0]), status_code=303)


@router.post("/ui/junk-holds/hold", response_model=None)
async def ui_junk_holds_hold(
    request: Request,
    db: Session = Depends(get_db),  # noqa: B008
) -> RedirectResponse:
    form = await _form(request)
    article_id = _article(form)
    apply_hold(db, article_id)
    return RedirectResponse(_back(form, article_id), status_code=303)


@router.post("/ui/junk-holds/reextract", response_model=None)
async def ui_junk_holds_reextract(
    request: Request,
    db: Session = Depends(get_db),  # noqa: B008
) -> RedirectResponse:
    form = await _form(request)
    article_id = _article(form)
    released = apply_reextract(db, article_id)
    location = _back(form, article_id)
    if released:
        path = location.partition("#")[0]
        location = f"{path}&{urlencode({'released': article_id})}"
    return RedirectResponse(location, status_code=303)
