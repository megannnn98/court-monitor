"""«Безымянные»: the figurants a publication does not name, and who on the Rosfinmonitoring
list they may be — for a person to confirm.

Each card: the sentence (to its publication), what the text tells (age, sex, place, the
surname's initial, the articles), the entries of the list that fit and why, and the
person's word: «Это он», «Не он», «Никого нет в перечне». The words are kept by key
(`entities.unnamed.decide`) through every new search."""

from __future__ import annotations

from datetime import datetime
from html import escape
from urllib.parse import parse_qs, quote, urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from db.orm_models import UnnamedFigurantRecord
from entities.unnamed import DIFFERENT, EVENT_LABELS, NONE, SAME, Candidates, candidates, decide
from web.dependencies import get_db
from web.ui.layout import _page, pager

router = APIRouter()

PAGE_SIZE = 20
_STATUSES = {
    "open": "Не разобраны",
    "found": "Опознаны",
    "none": "Никого в перечне",
    "all": "Все",
}
# Per figurant: confirmed (the candidate), or none on the list.
_WORDS = text(
    """
    SELECT figurant_key,
           max(candidate) FILTER (WHERE decision = 'same') AS same,
           bool_or(decision = 'none') AS none
    FROM unnamed_decisions GROUP BY figurant_key
    """
)


def _day(moment: datetime | None) -> str:
    return moment.astimezone().strftime("%d.%m.%Y") if moment else "—"


def _facts(figurant: UnnamedFigurantRecord) -> str:
    parts = []
    if figurant.age is not None:
        parts.append(f"{figurant.age} лет")
    if figurant.gender:
        parts.append("мужчина" if figurant.gender == "male" else "женщина")
    if figurant.place:
        parts.append(escape(figurant.place))
    if figurant.initial:
        parts.append(f"фамилия на «{escape(figurant.initial)}»")
    parts.append(escape(EVENT_LABELS.get(figurant.event_type, figurant.event_type)))
    if figurant.articles:
        parts.append("ст. " + ", ".join(escape(str(article)) for article in figurant.articles))
    return " · ".join(parts)


def _decision_form(
    figurant_key: str, candidate: str, decision: str, label: str, css: str, back: str
) -> str:
    return (
        '<form method="post" action="/ui/unnamed/decide" class="inline-form">'
        f'<input type="hidden" name="figurant" value="{escape(figurant_key, quote=True)}">'
        f'<input type="hidden" name="candidate" value="{escape(candidate, quote=True)}">'
        f'<input type="hidden" name="back" value="{escape(back, quote=True)}">'
        f'<button type="submit" name="decision" value="{decision}" class="{css}">{label}</button>'
        "</form>"
    )


def _candidates_html(figurant: UnnamedFigurantRecord, found: Candidates, back: str) -> str:
    if figurant.age is None:
        return '<p class="muted">Возраст не назван — по перечню не подобрать.</p>'
    if not found.shown:
        if not found.total:
            return (
                '<p class="muted">В перечне нет никого этого возраста и пола '
                f"(снимок от {_day(found.snapshot_date)}).</p>"
            )
        return (
            f'<p class="muted">В перечне {found.total} человек этого возраста и пола; без '
            "города рождения или буквы фамилии их не сузить.</p>"
        )
    rows = []
    for item in found.shown:
        verdict = (
            ' <span class="badge succeeded">это он</span>'
            if item.decision == SAME
            else ' <span class="badge">не он</span>'
            if item.decision == DIFFERENT
            else ""
        )
        actions = "".join(
            (
                _decision_form(figurant.key, item.key, SAME, "Это он", "", back)
                if item.decision != SAME
                else "",
                _decision_form(figurant.key, item.key, DIFFERENT, "Не он", "secondary", back)
                if item.decision != DIFFERENT
                else "",
            )
        )
        seen = (
            f"в перечне с {_day(item.first_seen)} или раньше"
            if item.first_seen
            else "дата включения неизвестна"
        )
        rows.append(
            f'<tr><th scope="row">{escape(item.full_name)}{verdict}</th>'
            f"<td>{item.birth_date:%d.%m.%Y}</td><td>{escape(item.birth_place)}</td>"
            f"<td>{escape('; '.join(item.reasons))}</td><td>{escape(seen)}</td>"
            f'<td class="actions-cell">{actions}</td></tr>'
        )
    more = (
        f'<p class="muted">Показаны {len(found.shown)} из {found.total} человек этого возраста '
        "и пола — те, кто родился в названном месте или подходит по букве фамилии.</p>"
        if found.total > len(found.shown)
        else ""
    )
    return f"""<table class="candidates"><caption>Кандидаты из перечня</caption>
<thead><tr><th scope="col">ФИО</th><th scope="col">Дата рождения</th>
<th scope="col">Место рождения</th><th scope="col">Почему подходит</th>
<th scope="col">В перечне</th><th scope="col">Решение</th></tr></thead>
<tbody>{"".join(rows)}</tbody></table>{more}"""


def _card(
    figurant: UnnamedFigurantRecord,
    found: Candidates,
    same: str | None,
    none: bool,
    back: str,
) -> str:
    status = (
        f'<span class="badge succeeded">опознан: {escape(same.split("|")[0].upper())}</span>'
        if same
        else '<span class="badge">никого в перечне</span>'
        if none
        else '<span class="badge pending">не разобран</span>'
    )
    none_button = (
        ""
        if none or same
        else _decision_form(figurant.key, "", NONE, "Никого нет в перечне", "secondary", back)
    )
    return f"""<article class="unnamed-card" id="u-{escape(figurant.key)}">
  <p class="badges">{status} <span class="muted">{_facts(figurant)}</span></p>
  <p class="quote"><a href="/ui/articles/{figurant.article_id}?start={
        figurant.start_offset
    }&amp;end={figurant.end_offset}">{escape(figurant.quote)}</a> <span class="muted">— {
        _day(figurant.published_at)
    }</span></p>
  <p class="muted">Модель: {escape(figurant.explanation)}</p>
  {_candidates_html(figurant, found, back)}
  {none_button}
</article>"""


@router.get("/ui/unnamed", response_class=HTMLResponse)
def ui_unnamed(
    status: str = Query(default="open", pattern="^(open|found|none|all)$"),
    page: int = Query(default=1, ge=1),
    db: Session = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    words = {key: (same, bool(none)) for key, same, none in db.execute(_WORDS).all()}
    figurants = list(
        db.scalars(
            select(UnnamedFigurantRecord).order_by(
                UnnamedFigurantRecord.published_at.desc().nulls_last(), UnnamedFigurantRecord.id
            )
        ).all()
    )

    def state(figurant: UnnamedFigurantRecord) -> str:
        same, none = words.get(figurant.key, (None, False))
        return "found" if same else "none" if none else "open"

    counts = {
        key: sum(state(item) == key for item in figurants) for key in ("open", "found", "none")
    }
    counts["all"] = len(figurants)
    chosen = [item for item in figurants if status == "all" or state(item) == status]
    on_page = chosen[(page - 1) * PAGE_SIZE : page * PAGE_SIZE]
    back = urlencode({"status": status, "page": page})
    cards = "".join(
        _card(item, candidates(db, item), *words.get(item.key, (None, False)), back)
        for item in on_page
    )
    chips = " ".join(
        f'<a class="chip{" active" if key == status else ""}" '
        f'href="/ui/unnamed?{urlencode({"status": key})}">{label} ({counts[key]})</a>'
        for key, label in _STATUSES.items()
    )
    empty = (
        '<p class="empty">Безымянных фигурантов пока нет: их находит шаг 6 «Отобрать '
        "политические дела».</p>"
        if not figurants
        else '<p class="empty">В этом разделе никого.</p>'
    )
    pages = (len(chosen) + PAGE_SIZE - 1) // PAGE_SIZE
    body = f"""<p class="chips">{chips}</p>
<p class="muted">Люди, которых публикации не называют («17-летний житель Тюмени»), и кто из
перечня Росфинмониторинга может быть ими: того возраста на дату новости (или на год старше —
событие бывает раньше новости), того пола и буквы фамилии; сначала — родившиеся в названном
месте. Место рождения — не всегда место жительства; даты включения в перечень у нас есть
только с первого сохранённого снимка.</p>
{cards or empty}
{pager("/ui/unnamed", {"status": status}, page, pages)}"""
    return _page(
        "Безымянные",
        body,
        active="unnamed",
        instruction=(
            "Безымянные фигуранты из пресс-релизов и новостей и кандидаты на них из перечня "
            "Росфинмониторинга."
        ),
        next_action=(
            "Сравните кандидатов с текстом; «Это он» опознаёт человека, «Никого нет в "
            "перечне» закрывает случай."
        ),
        db=db,
    )


@router.post("/ui/unnamed/decide", response_model=None)
async def decide_unnamed(
    request: Request,
    db: Session = Depends(get_db),  # noqa: B008
) -> RedirectResponse:
    form = {
        key: values[0]
        for key, values in parse_qs(
            (await request.body()).decode("utf-8", errors="replace")
        ).items()
    }
    figurant = form.get("figurant", "")
    decision = form.get("decision", "")
    known = db.scalar(
        select(func.count())
        .select_from(UnnamedFigurantRecord)
        .where(UnnamedFigurantRecord.key == figurant)
    )
    if not known or decision not in (SAME, DIFFERENT, NONE):
        raise HTTPException(status_code=400, detail="Неполное решение")
    if decision != NONE and not form.get("candidate"):
        raise HTTPException(status_code=400, detail="Не выбран человек из перечня")
    decide(db, figurant, form.get("candidate", ""), decision)
    db.commit()
    back = form.get("back", "")
    # Only the page's own state goes back into the address.
    kept = {key: value for key, value in (item.partition("=")[::2] for item in back.split("&"))}
    params = {
        "status": kept.get("status", "open") if kept.get("status") in _STATUSES else "open",
        "page": kept.get("page", "1") if kept.get("page", "").isdigit() else "1",
    }
    return RedirectResponse(f"/ui/unnamed?{urlencode(params)}#u-{quote(figurant)}", status_code=303)
