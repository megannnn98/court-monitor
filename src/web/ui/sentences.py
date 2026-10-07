"""«Приговоры»: the sentences a model read from the articles (`entities.sentences`),
folded into cases (`entities.sentence_cases`) — what «Спросить» counts over.

Each case: who, where, what punishment, for what, the article's own words, and a
person's way to take a wrong one out of every count («Неверно»). What was taken out is
a list of its own, to be put back.
"""

from __future__ import annotations

from html import escape
from typing import Any
from urllib.parse import parse_qs, urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session

from db.orm_models import ArticleSentenceReadingRecord, ArticleSentenceRecord
from entities.ask import KIND_LABELS, REASON_LABELS
from entities.regions import canonical
from entities.sentence_cases import POLITICAL, Case, Filters, cases
from entities.sentence_cases import select as select_cases
from web.dependencies import get_db
from web.ui.layout import _page, pager

router = APIRouter()

PAGE_SIZE = 50
HIDDEN = "hidden"

_HIDDEN_ROWS = text(
    """
    SELECT t.id, t.article_id, t.person, t.region, t.kind, t.months, t.fine_rub,
           t.in_absentia, t.sentenced_on, t.reason, t.reason_text, t.quote, a.title
    FROM article_sentences t JOIN parsed_articles a ON a.id = t.article_id
    WHERE t.hidden ORDER BY t.id DESC
    """
)


def _term(months: int, fine_rub: int) -> str:
    years, rest = divmod(months, 12)
    parts = [f"{years} г." if years else "", f"{rest} мес." if rest else ""]
    if fine_rub:
        parts.append(f"штраф {fine_rub:,} ₽".replace(",", " "))
    return " ".join(part for part in parts if part) or "—"


def _button(action: str, rows: tuple[int, ...] | list[int], label: str, back: str) -> str:
    return (
        f'<form method="post" action="/ui/sentences/{action}" class="inline-form">'
        f'<input type="hidden" name="rows" value="{",".join(map(str, rows))}">'
        f'<input type="hidden" name="back" value="{escape(back, quote=True)}">'
        f'<button type="submit" class="secondary">{label}</button></form>'
    )


def _case_row(case: Case, back: str) -> str:
    more = f" и ещё {len(case.publications) - 1}" if len(case.publications) > 1 else ""
    absentia = " (заочно)" if case.in_absentia else ""
    return (
        f"<tr><td>{escape(case.person)}</td><td>{escape(case.region or '—')}</td>"
        f"<td>{escape(KIND_LABELS[case.kind] + absentia)}</td>"
        f"<td>{escape(_term(case.months, case.fine_rub))}</td>"
        f"<td>{escape(case.sentenced_on or '—')}</td>"
        f"<td>{escape(REASON_LABELS[case.reason])}"
        f'<br><span class="muted">{escape(case.reason_text)}</span></td>'
        f'<td><a href="/ui/articles/{case.article_id}">{escape(case.title)}</a>'
        f'<br><span class="muted">{escape(case.source + more)}</span>'
        f"<br>«{escape(case.quote)}»</td>"
        f"<td>{_button('hide', case.row_ids, 'Неверно', back)}</td></tr>"
    )


def _hidden_row(row: Any, back: str) -> str:
    return (
        f"<tr><td>{escape(row.person)}</td><td>{escape(row.region or '—')}</td>"
        f"<td>{escape(KIND_LABELS.get(row.kind, row.kind))}</td>"
        f"<td>{escape(_term(row.months, row.fine_rub))}</td>"
        f"<td>{escape(row.sentenced_on or '—')}</td>"
        f"<td>{escape(REASON_LABELS.get(row.reason, row.reason))}"
        f'<br><span class="muted">{escape(row.reason_text)}</span></td>'
        f'<td><a href="/ui/articles/{row.article_id}">{escape(row.title)}</a>'
        f"<br>«{escape(row.quote)}»</td>"
        f"<td>{_button('show', [row.id], 'Вернуть', back)}</td></tr>"
    )


_HEAD = (
    '<thead><tr><th scope="col">Человек</th><th scope="col">Регион</th>'
    '<th scope="col">Наказание</th><th scope="col">Срок</th><th scope="col">Дата</th>'
    '<th scope="col">За что</th><th scope="col">Публикация и её слова</th>'
    '<th scope="col"><span class="visually-hidden">Действие</span></th></tr></thead>'
)


@router.get("/ui/sentences", response_class=HTMLResponse)
def ui_sentences(
    reason: str = Query(default=POLITICAL, max_length=32),
    region: str = Query(default="", max_length=64),
    view: str = Query(default="", pattern=f"^({HIDDEN})?$"),
    page: int = Query(default=1, ge=1),
    db: Session = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    all_cases = cases(db)
    reason = reason if reason in ("", POLITICAL, *REASON_LABELS) else POLITICAL
    region = canonical(region)
    params = {"reason": reason, "region": region}
    hidden_rows = db.execute(_HIDDEN_ROWS).all()
    if view == HIDDEN:
        back = urlencode({**params, "view": HIDDEN})
        rows = "".join(_hidden_row(row, back) for row in hidden_rows)
        total, pages = len(hidden_rows), 1
        empty = "Ничего не убрано."
    else:
        found = select_cases(
            all_cases, Filters(reasons=(reason,) if reason else (), region=region)
        ).cases
        found.sort(key=lambda case: (case.sentenced_on, case.row_ids), reverse=True)
        total = len(found)
        pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
        back = urlencode({**params, "page": page})
        rows = "".join(
            _case_row(case, back) for case in found[(page - 1) * PAGE_SIZE : page * PAGE_SIZE]
        )
        empty = "Таких приговоров нет."
    read = db.scalar(select(func.count()).select_from(ArticleSentenceReadingRecord)) or 0
    reasons = "".join(
        f'<option value="{key}"{" selected" if key == reason else ""}>{escape(label)}</option>'
        for key, label in (
            ("", "Все дела"),
            (POLITICAL, "Все политические"),
            *REASON_LABELS.items(),
        )
    )
    regions = "".join(
        f'<option value="{escape(name, quote=True)}"{" selected" if name == region else ""}>'
        f"{escape(name)}</option>"
        for name in sorted({case.region for case in all_cases if case.region})
    )
    chips = (
        f'<a class="chip{"" if view else " active"}" href="/ui/sentences?{urlencode(params)}">'
        f"Приговоры</a>"
        f'<a class="chip{" active" if view else ""}" '
        f'href="/ui/sentences?{urlencode({**params, "view": HIDDEN})}">'
        f"Убранные: {len(hidden_rows)}</a>"
    )
    table = (
        f'<table class="sticky-head"><caption class="visually-hidden">Приговоры</caption>'
        f"{_HEAD}<tbody>{rows}</tbody></table>"
        if rows
        else f'<p class="empty">{empty}</p>'
    )
    body = f"""<p class="muted">Модель прочла публикаций с приговором: {read}. Из них выписано
дел: {len(all_cases)}. Один приговор, о котором написали несколько источников, — одно дело.
По этим делам считает страница <a href="/ui/ask">«Спросить»</a>.</p>
<p class="chips">{chips}</p>
<form method="get" class="toolbar">
  <label for="sentence-reason" class="visually-hidden">Причина преследования</label>
  <select id="sentence-reason" name="reason">{reasons}</select>
  <label for="sentence-region" class="visually-hidden">Регион</label>
  <select id="sentence-region" name="region"><option value="">Все регионы</option>{regions}</select>
  <button type="submit">Показать</button>
</form>
<p class="muted">Найдено: {total}.</p>
<section class="band table-band">{table}</section>
{pager("/ui/sentences", {key: str(value) for key, value in params.items()}, page, pages)}"""
    return _page(
        "Приговоры",
        body,
        active="sentences",
        instruction=(
            "Приговоры, которые модель выписала из публикаций: регион суда, наказание и "
            "причина преследования, каждое — со словами самой публикации. Если запись "
            "неверна, нажмите «Неверно»: она уйдёт из всех подсчётов."
        ),
        db=db,
    )


async def _set_hidden(request: Request, db: Session, hidden: bool) -> RedirectResponse:
    form = parse_qs((await request.body()).decode("utf-8", errors="replace"))
    ids = {int(part) for part in form.get("rows", [""])[0].split(",") if part.isdigit()}
    if not ids:
        raise HTTPException(status_code=400, detail="Не указан приговор")
    changed = db.execute(
        update(ArticleSentenceRecord)
        .where(ArticleSentenceRecord.id.in_(ids))
        .values(hidden=hidden)
        .returning(ArticleSentenceRecord.id)
    ).all()
    if not changed:
        raise HTTPException(status_code=404, detail="Такого приговора нет")
    if len(changed) != len(ids):
        # The page is older than the rows: a part of a case must not go alone. Nothing
        # is committed, so nothing is hidden.
        raise HTTPException(
            status_code=409, detail="Список приговоров изменился. Обновите страницу."
        )
    db.commit()
    kept = parse_qs(form.get("back", [""])[0])
    params: dict[str, str] = {
        key: kept[key][0] for key in ("reason", "region", "view", "page") if key in kept
    }
    return RedirectResponse(f"/ui/sentences?{urlencode(params)}", status_code=303)


@router.post("/ui/sentences/hide", response_model=None)
async def ui_sentences_hide(
    request: Request,
    db: Session = Depends(get_db),  # noqa: B008
) -> RedirectResponse:
    return await _set_hidden(request, db, True)


@router.post("/ui/sentences/show", response_model=None)
async def ui_sentences_show(
    request: Request,
    db: Session = Depends(get_db),  # noqa: B008
) -> RedirectResponse:
    return await _set_hidden(request, db, False)
