"""«Без имени в базе»: the nameless records of the operator's own base, and who on the
Rosfinmonitoring list they may be — for a person to confirm.

Each card: the record as the base writes it, what the base says of the case (sex, place,
articles, the day it was opened), the entries of the list that fit and why, and the
person's word: «Это он», «Не он». The record with the freshest candidate stands first,
so what a new snapshot of the list brought is on top."""

from __future__ import annotations

from html import escape
from urllib.parse import parse_qs, quote, urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from db.orm_models import AirtableKnownPersonRecord
from entities.base_candidates import article_numbers
from entities.base_unnamed import NamelessCase, counts, nameless_cases, say
from entities.jurisdiction import Jurisdiction
from entities.unnamed import CANDIDATE_KEY_LENGTH, DIFFERENT, SAME
from web.dependencies import get_db
from web.ui.court_hints import courts_html
from web.ui.layout import _page, pager

router = APIRouter()

PAGE_SIZE = 20
_STATUSES = {"open": "Не разобраны", "found": "Опознаны", "all": "Все"}
_CLEAR = "clear"


def _facts(case: NamelessCase) -> str:
    record = case.record
    parts = [f"{case.age} лет"] if case.age is not None else []
    if record.gender:
        parts.append("мужчина" if record.gender == "male" else "женщина")
    place = ", ".join(part for part in (record.region, record.city) if part)
    if place:
        parts.append(escape(place))
    if record.case_opened_on:
        parts.append(f"дело возбуждено {record.case_opened_on:%d.%m.%Y}")
    if record.articles:
        parts.append(escape(record.articles))
    return " · ".join(parts)


def _word_form(
    record_id: str, candidate: str, decision: str, label: str, css: str, back: str
) -> str:
    return (
        '<form method="post" action="/ui/base-unnamed/decide" class="inline-form">'
        f'<input type="hidden" name="record" value="{escape(record_id, quote=True)}">'
        f'<input type="hidden" name="candidate" value="{escape(candidate, quote=True)}">'
        f'<input type="hidden" name="decision" value="{decision}">'
        f'<input type="hidden" name="back" value="{escape(back, quote=True)}">'
        f'<button type="submit" class="{css}">{label}</button></form>'
    )


def _card(case: NamelessCase, courts: Jurisdiction, back: str) -> str:
    record = case.record
    identified = case.identified
    status = (
        f'<span class="badge succeeded">опознан: {escape(identified.entry.full_name)}</span>'
        if identified
        else '<span class="badge succeeded">опознан: '
        f"{escape(case.confirmed.split('|')[0])}</span>"
        ' <span class="muted">этой записи сейчас нет среди кандидатов из перечня</span>'
        if case.confirmed
        else '<span class="badge pending">не разобран</span>'
        if case.open
        else '<span class="badge">кандидаты отклонены</span>'
    )
    rows = []
    for item in case.candidates:
        verdict = (
            ' <span class="badge succeeded">это он</span>'
            if item.decision == SAME
            else ' <span class="badge">не он</span>'
            if item.decision == DIFFERENT
            else ""
        )
        key = item.entry.key
        actions = (
            (
                _word_form(record.external_id, key, SAME, "Это он", "", back)
                if item.decision != SAME
                else ""
            )
            + (
                _word_form(record.external_id, key, DIFFERENT, "Не он", "secondary", back)
                if item.decision != DIFFERENT
                else ""
            )
            + (
                _word_form(record.external_id, key, _CLEAR, "Отменить", "secondary", back)
                if item.decision
                else ""
            )
        )
        rows.append(
            f'<tr><th scope="row">{escape(item.entry.full_name)}{verdict}</th>'
            f"<td>{item.entry.birth_date:%d.%m.%Y}</td><td>{escape(item.entry.birth_place)}</td>"
            f"<td>{escape('; '.join(item.reasons))}</td>"
            f'<td class="actions-cell">{actions}</td></tr>'
        )
    table = (
        f"""<table class="candidates"><caption>Кандидаты из перечня</caption>
<thead><tr><th scope="col">ФИО</th><th scope="col">Дата рождения</th>
<th scope="col">Место рождения</th><th scope="col">Почему подходит</th>
<th scope="col">Решение</th></tr></thead>
<tbody>{"".join(rows)}</tbody></table>"""
        if rows
        else ""
    )
    # A confirmed entry that is not among the candidates has no row to take the word back in.
    forget = (
        '<p class="actions-cell">'
        + _word_form(
            record.external_id, case.confirmed, _CLEAR, "Отменить решение", "secondary", back
        )
        + "</p>"
        if case.confirmed and identified is None
        else ""
    )
    more = (
        f'<p class="muted">Показаны {len(case.candidates)} из {case.total} подходящих записей '
        "перечня: сначала родившиеся в названном городе, затем точный возраст и самые новые.</p>"
        if case.total > len(case.candidates)
        else ""
    )
    return f"""<article class="unnamed-card" id="b-{escape(record.external_id, quote=True)}">
  <p class="badges">{status}</p>
  <p class="quote">{escape(record.full_name)}</p>
  <p class="muted">{_facts(case)}</p>
  {
        courts_html(
            courts.courts(
                f"{record.region or ''} {record.city or ''}", article_numbers(record.articles or "")
            )
        )
    }
  {table}{more}{forget}
</article>"""


@router.get("/ui/base-unnamed", response_class=HTMLResponse)
def ui_base_unnamed(
    status: str = Query(default="open", pattern="^(open|found|all)$"),
    page: int = Query(default=1, ge=1),
    db: Session = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    cases = nameless_cases(db)
    totals = counts(cases)
    chosen = [
        case
        for case in cases
        if status == "all"
        or (status == "found" and case.confirmed is not None)
        or (status == "open" and case.confirmed is None and case.open)
    ]
    back = urlencode({"status": status, "page": page})
    courts = Jurisdiction.from_session(db)
    cards = "".join(
        _card(case, courts, back) for case in chosen[(page - 1) * PAGE_SIZE : page * PAGE_SIZE]
    )
    chips = " ".join(
        f'<a class="chip{" active" if key == status else ""}" '
        f'href="/ui/base-unnamed?{urlencode({"status": key})}">{label} ({totals[key]})</a>'
        for key, label in _STATUSES.items()
    )
    empty = (
        '<p class="empty">Записей без имени с кандидатами из перечня нет. Нужны синхронизация '
        "с Airtable и загруженный перечень Росфинмониторинга.</p>"
        if not cases
        else '<p class="empty">В этом разделе никого.</p>'
    )
    body = f"""<p><a href="/ui/unnamed">Безымянные из новостей</a></p>
<p class="chips">{chips}</p>
<p class="muted">Записи базы Airtable без имени («34-летний уроженец Крыма») и кто из перечня
Росфинмониторинга может быть ими: того возраста на день возбуждения дела (плюс-минус год), того
пола, родился в названном городе или регионе. Включённые в перечень после возбуждения дела стоят
выше включённых до него. Первыми идут записи, у которых кандидат появился в перечне позже всех. Место рождения — не
всегда место жительства. Имя в базу вносит оператор; здесь решение только запоминается.</p>
{cards or empty}
{pager("/ui/base-unnamed", {"status": status}, page, (len(chosen) + PAGE_SIZE - 1) // PAGE_SIZE)}"""
    return _page(
        "Без имени в базе",
        body,
        active="base_unnamed",
        instruction=(
            "Записи базы Airtable без имени и кандидаты на них из перечня Росфинмониторинга."
        ),
        db=db,
    )


@router.post("/ui/base-unnamed/decide", response_model=None)
async def decide_base_unnamed(
    request: Request,
    db: Session = Depends(get_db),  # noqa: B008
) -> RedirectResponse:
    form = {
        key: values[0]
        for key, values in parse_qs(
            (await request.body()).decode("utf-8", errors="replace")
        ).items()
    }
    record = form.get("record", "")
    candidate = form.get("candidate", "")
    decision = form.get("decision", "")
    known = db.scalar(
        select(func.count())
        .select_from(AirtableKnownPersonRecord)
        .where(AirtableKnownPersonRecord.external_id == record)
    )
    if not known:
        raise HTTPException(status_code=400, detail="Неизвестная запись базы")
    if not 0 < len(candidate) <= CANDIDATE_KEY_LENGTH or decision not in (SAME, DIFFERENT, _CLEAR):
        raise HTTPException(status_code=400, detail="Неполное решение")
    say(db, record, candidate, None if decision == _CLEAR else decision)
    db.commit()
    kept = dict(item.partition("=")[::2] for item in form.get("back", "").split("&"))
    params = {
        "status": kept.get("status") if kept.get("status") in _STATUSES else "open",
        "page": kept.get("page", "1") if kept.get("page", "").isdigit() else "1",
    }
    return RedirectResponse(
        f"/ui/base-unnamed?{urlencode(params)}#b-{quote(record)}", status_code=303
    )
