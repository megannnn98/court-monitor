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
_COUNTS = text("SELECT status, count(*) FROM junk_screen_holds GROUP BY status")


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


@router.get("/ui/junk-holds", response_class=HTMLResponse)
def ui_junk_holds(
    status: str = Query(default=HELD, pattern=f"^({HELD}|{JUNK})$"),
    page: int = Query(default=1, ge=1),
    released: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    counts = {str(key): int(value) for key, value in db.execute(_COUNTS).all()}
    rows = db.execute(_HOLDS, {"status": status}).all()
    on_page = rows[(page - 1) * PAGE_SIZE : page * PAGE_SIZE]
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
{"".join(_card(row, status, page) for row in on_page) or empty}
{pager("/ui/junk-holds", {"status": status}, page, (len(rows) + PAGE_SIZE - 1) // PAGE_SIZE)}"""
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
