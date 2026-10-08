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

from monitoring.hold_reader import MODEL_JUNK, RELEASED, release
from monitoring.junk_holds import hold_again, mark_junk, reextract, unrelease
from monitoring.junk_screen import HELD, JUNK
from web.dependencies import get_db, session_factory_for
from web.ui.layout import _page, pager

router = APIRouter()

PAGE_SIZE = 30
# The held are two lists: what a model read as junk waits apart, to go by one press.
MODEL_JUNK_VIEW = "model_junk"
_STATUSES = {
    HELD: "На проверке",
    MODEL_JUNK_VIEW: "Модель считает мусором",
    RELEASED: "Выпущено в работу",
    JUNK: "Отмечены как мусор",
}
# A list's rows: the hold's status, and for the held — whether a model called it junk.
_VIEW = (
    "h.status = :status AND (CAST(:model_junk AS boolean) IS NULL "
    "OR coalesce(h.reader_verdict = :junk_read, false) = CAST(:model_junk AS boolean))"
)


def _view(status: str) -> dict[str, object]:
    return {
        "status": HELD if status == MODEL_JUNK_VIEW else status,
        "model_junk": {HELD: False, MODEL_JUNK_VIEW: True}.get(status),
        "junk_read": MODEL_JUNK,
    }


_HOLDS = text(
    f"""
    SELECT h.article_id, h.status, h.score, h.cutoff, h.screen, h.reason, h.note,
           h.reader_verdict,
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
    WHERE {_VIEW}
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
    f"""
    WITH latest AS (
        SELECT DISTINCT ON (r.article_id) r.article_id, r.id
        FROM article_extraction_runs r
        JOIN junk_screen_holds h ON h.article_id = r.article_id AND {_VIEW}
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
    f"""
    WITH held AS (
        SELECT a.id, left(a.title, 300) AS title
        FROM junk_screen_holds h JOIN parsed_articles a ON a.id = h.article_id
        WHERE {_VIEW} AND a.title IS NOT NULL AND length(a.title) >= 20
    )
    SELECT x.id, y.id FROM held x JOIN held y ON x.id < y.id
    WHERE similarity(x.title, y.title) >= :similarity
    """
)
_COUNTS = text(
    """
    SELECT CASE WHEN status = 'held' AND reader_verdict = 'junk' THEN 'model_junk' ELSE status END,
           count(*)
    FROM junk_screen_holds GROUP BY 1
    """
)


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


# Whose word the note is, when it is a model's.
_NOTE_LABELS = {MODEL_JUNK: "Модель: в работу не брать — ", "case": "Модель: дело — "}


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


def junk_counts(db: Session) -> dict[str, int]:
    return {str(key): int(value) for key, value in db.execute(_COUNTS).all()}


def default_status(counts: dict[str, int]) -> str:
    """Opened from the queue: the list there is work in, the model's when it is all."""
    return MODEL_JUNK_VIEW if not counts.get(HELD) and counts.get(MODEL_JUNK_VIEW) else HELD


def held_stories(db: Session, status: str) -> tuple[dict[int, Any], list[list[list[int]]]]:
    """The list's rows by article, and its stories in pages; read by this page and the API."""
    rows = db.execute(_HOLDS, _view(status)).all()
    by_id = {row.article_id: row for row in rows}
    pairs = [
        (first, second)
        for first, second in [
            *db.execute(_SAME_PERSON, _view(status)).all(),
            *db.execute(_SAME_TITLE, {**_view(status), "similarity": TITLE_SIMILARITY}).all(),
        ]
    ]
    return by_id, _pages(same_news(list(by_id), pairs))


def note_label(reader_verdict: str | None) -> str:
    return _NOTE_LABELS.get(reader_verdict or "", "")


def apply_junk(db: Session, article_id: int) -> None:
    """«Мусор» of a held article; commits."""
    if not mark_junk(db, article_id):
        raise HTTPException(status_code=404, detail="Статья не на проверке")
    db.commit()


def apply_unrelease(db: Session, article_id: int) -> None:
    """«Мусор» of a released article: under the purge again; commits."""
    if not unrelease(db, article_id):
        raise HTTPException(status_code=404, detail="Статья не выпущена в работу")
    db.commit()


def apply_release(db: Session, article_id: int) -> None:
    """«Это дело — в работу»; commits."""
    if not release(db, article_id):
        raise HTTPException(status_code=404, detail="Статья не на проверке")
    db.commit()


def apply_junk_all(db: Session, articles: list[int]) -> None:
    """«Мусор» for every article of one story. All of them, or none: one that is no
    longer held means the page is stale, and the operator should look again."""
    if not articles:
        raise HTTPException(status_code=400, detail="Не указаны статьи")
    if not all(mark_junk(db, article_id) for article_id in articles):
        db.rollback()
        raise HTTPException(status_code=404, detail="Статья не на проверке")
    db.commit()


def apply_hold(db: Session, article_id: int) -> None:
    """«Вернуть на проверку» of an article marked junk; commits."""
    if not hold_again(db, article_id):
        raise HTTPException(status_code=404, detail="Статья не отмечена как мусор")
    db.commit()


def apply_reextract(db: Session, article_id: int) -> bool:
    """«Извлечь заново»: whether an event was found and the article went back to work."""
    held = db.scalar(
        text("SELECT status FROM junk_screen_holds WHERE article_id = :article"),
        {"article": article_id},
    )
    if held != HELD:
        raise HTTPException(status_code=404, detail="Статья не на проверке")
    return reextract(session_factory_for(db), article_id).released


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
