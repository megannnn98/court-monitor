"""«Безымянные»: the figurants a publication does not name, and who on the Rosfinmonitoring
list they may be — for a person to confirm.

Each card: the sentence (to its publication), what the text tells (age, sex, place, the
surname's initial, the articles), the entries of the list that fit and why, and the
person's word: «Это он», «Не он», «Никого нет в перечне». The words are kept by key
(`entities.unnamed.decide`) through every new search."""

from __future__ import annotations

from datetime import date, datetime
from html import escape
from urllib.parse import parse_qs, quote, urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from db.orm_models import EntityGroupRecord, UnnamedFigurantRecord
from entities.unnamed import (
    DIFFERENT,
    EVENT_LABELS,
    EXISTING_PERSON,
    IDENTIFIED,
    INSUFFICIENT,
    NO_RF_MATCH,
    RF_ENTRY,
    SAME,
    SUPPLIED_NAME,
    Candidates,
    candidates,
    clear_resolution,
    decide,
    resolve_identity,
)
from web.dependencies import get_db
from web.ui.layout import _page, pager

router = APIRouter()

PAGE_SIZE = 20
_STATUSES = {
    "open": "Не разобраны",
    "found": "Опознаны",
    "no_rf": "Нет записи РФМ",
    "insufficient": "Недостаточно данных",
    "all": "Все",
}
# Per figurant: the stable operator identification.
_WORDS = text(
    """
    SELECT figurant_key, resolution, normalized_name, existing_person_key,
           rf_name, rf_birth_date, decided_at
    FROM unnamed_identity_resolutions
    """
)


def _day(moment: datetime | None) -> str:
    return moment.astimezone().strftime("%d.%m.%Y") if moment else "—"


def _rf_display_name(full_name: str) -> str:
    return " ".join(word.strip("*").lower().capitalize() for word in full_name.split())


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


def _post_form(
    action: str,
    figurant_key: str,
    label: str,
    css: str,
    back: str,
    hidden: dict[str, str],
) -> str:
    inputs = "".join(
        f'<input type="hidden" name="{escape(name, quote=True)}" '
        f'value="{escape(value, quote=True)}">'
        for name, value in hidden.items()
    )
    return (
        f'<form method="post" action="{action}" class="inline-form">'
        f'<input type="hidden" name="figurant" value="{escape(figurant_key, quote=True)}">'
        f'<input type="hidden" name="back" value="{escape(back, quote=True)}">'
        f"{inputs}"
        f'<button type="submit" class="{css}">{label}</button>'
        "</form>"
    )


def _reject_form(figurant_key: str, candidate: str, back: str) -> str:
    return _post_form(
        "/ui/unnamed/reject",
        figurant_key,
        "Не он",
        "secondary",
        back,
        {"candidate": candidate},
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
        actions = (
            _post_form(
                "/ui/unnamed/resolve",
                figurant.key,
                "Это он",
                "",
                back,
                {
                    "resolution": RF_ENTRY,
                    "normalized_name": _rf_display_name(item.full_name),
                    "rf_name": item.key.split("|")[0],
                    "rf_birth_date": item.birth_date.isoformat(),
                },
            )
            if item.decision != SAME
            else ""
        ) + (_reject_form(figurant.key, item.key, back) if item.decision != DIFFERENT else "")
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
    resolution: tuple[str, str | None, str | None, str | None, date | None, datetime | None] | None,
    people: list[EntityGroupRecord],
    back: str,
) -> str:
    kind, normalized_name, existing_key, rf_name, rf_birth_date, decided_at = resolution or (
        "",
        None,
        None,
        None,
        None,
        None,
    )
    status = (
        f'<span class="badge succeeded">опознан: {escape(normalized_name or rf_name or existing_key or "")}</span>'
        if kind in IDENTIFIED
        else '<span class="badge">подходящей записи РФМ нет</span>'
        if kind == NO_RF_MATCH
        else '<span class="badge">недостаточно данных</span>'
        if kind == INSUFFICIENT
        else '<span class="badge pending">не разобран</span>'
    )
    note = ""
    if kind in IDENTIFIED:
        source = (
            f"запись РФМ {escape(rf_name or '')}, {rf_birth_date:%d.%m.%Y}"
            if kind == RF_ENTRY and rf_birth_date
            else "существующий человек"
            if kind == EXISTING_PERSON
            else "имя указано вручную"
        )
        note = (
            f'<p class="muted">Опознан оператором: {source}'
            f"{f', {_day(decided_at)}' if decided_at else ''}.</p>"
        )
    person_options = "".join(
        f'<option value="{escape(person.key, quote=True)}">{escape(person.name)}</option>'
        for person in people
    )
    person_form = (
        '<form method="post" action="/ui/unnamed/resolve" class="toolbar">'
        f'<input type="hidden" name="figurant" value="{escape(figurant.key, quote=True)}">'
        f'<input type="hidden" name="back" value="{escape(back, quote=True)}">'
        f'<input type="hidden" name="resolution" value="{EXISTING_PERSON}">'
        '<select name="existing_person_key" required>'
        '<option value="">Найти существующего человека</option>'
        f'{person_options}</select><button type="submit" class="secondary">Это он</button></form>'
        if people
        else ""
    )
    supplied_form = (
        '<form method="post" action="/ui/unnamed/resolve" class="toolbar">'
        f'<input type="hidden" name="figurant" value="{escape(figurant.key, quote=True)}">'
        f'<input type="hidden" name="back" value="{escape(back, quote=True)}">'
        f'<input type="hidden" name="resolution" value="{SUPPLIED_NAME}">'
        '<label>Имя <input type="text" name="normalized_name" maxlength="200" required></label>'
        '<button type="submit" class="secondary">Создать человека</button></form>'
    )
    no_rf = _post_form(
        "/ui/unnamed/resolve",
        figurant.key,
        "Подходящей записи РФМ нет",
        "secondary",
        back,
        {"resolution": NO_RF_MATCH},
    )
    insufficient = _post_form(
        "/ui/unnamed/resolve",
        figurant.key,
        "Недостаточно данных",
        "secondary",
        back,
        {"resolution": INSUFFICIENT},
    )
    clear = (
        _post_form("/ui/unnamed/clear", figurant.key, "Отменить решение", "secondary", back, {})
        if kind
        else ""
    )
    return f"""<article class="unnamed-card" id="u-{escape(figurant.key)}">
  <p class="badges">{status} <span class="muted">{_facts(figurant)}</span></p>
  <p class="quote"><a href="/ui/articles/{figurant.article_id}?start={
        figurant.start_offset
    }&amp;end={figurant.end_offset}">{escape(figurant.quote)}</a> <span class="muted">— {
        _day(figurant.published_at)
    }</span></p>
  <p class="muted">Модель: {escape(figurant.explanation)}</p>
  {note}
  {_candidates_html(figurant, found, back)}
  {person_form}
  {supplied_form}
  <p class="actions-cell">{no_rf}{insufficient}{clear}</p>
</article>"""


@router.get("/ui/unnamed", response_class=HTMLResponse)
def ui_unnamed(
    status: str = Query(default="open", pattern="^(open|found|no_rf|insufficient|all)$"),
    page: int = Query(default=1, ge=1),
    person_q: str = Query(default="", max_length=100),
    db: Session = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    words = {
        key: (resolution, normalized_name, existing_key, rf_name, rf_birth_date, decided_at)
        for key, resolution, normalized_name, existing_key, rf_name, rf_birth_date, decided_at in db.execute(
            _WORDS
        ).all()
    }
    figurants = list(
        db.scalars(
            select(UnnamedFigurantRecord).order_by(
                UnnamedFigurantRecord.published_at.desc().nulls_last(), UnnamedFigurantRecord.id
            )
        ).all()
    )

    def state(figurant: UnnamedFigurantRecord) -> str:
        resolution = words.get(figurant.key, ("", None, None, None, None, None))[0]
        if resolution in IDENTIFIED:
            return "found"
        if resolution == INSUFFICIENT:
            return "insufficient"
        return "open"

    counts = {
        key: sum(state(item) == key for item in figurants)
        for key in ("open", "found", "insufficient")
    }
    counts["no_rf"] = sum(
        words.get(item.key, ("", None, None, None, None, None))[0] == NO_RF_MATCH
        for item in figurants
    )
    counts["all"] = len(figurants)
    chosen = [
        item
        for item in figurants
        if status == "all"
        or state(item) == status
        or (
            status == "no_rf"
            and words.get(item.key, ("", None, None, None, None, None))[0] == NO_RF_MATCH
        )
    ]
    on_page = chosen[(page - 1) * PAGE_SIZE : page * PAGE_SIZE]
    people = (
        list(
            db.scalars(
                select(EntityGroupRecord)
                .where(EntityGroupRecord.name.ilike(f"%{person_q.strip()}%"))
                .order_by(EntityGroupRecord.mention_count.desc(), EntityGroupRecord.name)
                .limit(20)
            )
        )
        if person_q.strip()
        else []
    )
    back = urlencode({"status": status, "page": page, "person_q": person_q.strip()})
    cards = "".join(
        _card(item, candidates(db, item), words.get(item.key), people, back) for item in on_page
    )
    chips = " ".join(
        f'<a class="chip{" active" if key == status else ""}" '
        f'href="/ui/unnamed?{urlencode({"status": key, "person_q": person_q.strip()})}">{label} ({counts[key]})</a>'
        for key, label in _STATUSES.items()
    )
    empty = (
        '<p class="empty">Безымянных фигурантов пока нет: их находит шаг 5 «Отобрать '
        "политические дела».</p>"
        if not figurants
        else '<p class="empty">В этом разделе никого.</p>'
    )
    pages = (len(chosen) + PAGE_SIZE - 1) // PAGE_SIZE
    search = f"""<form method="get" class="toolbar" role="search">
  <input type="hidden" name="status" value="{escape(status, quote=True)}">
  <input type="search" name="person_q" value="{escape(person_q, quote=True)}"
    placeholder="Имя существующего человека">
  <button type="submit" class="secondary">Найти</button>
</form>"""
    body = f"""<p class="chips">{chips}</p>
{search}
<p class="muted">Люди, которых публикации не называют («17-летний житель Тюмени»), и кто из
перечня Росфинмониторинга может быть ими: того возраста на дату новости (или на год старше —
событие бывает раньше новости), того пола и буквы фамилии; сначала — родившиеся в названном
месте. Место рождения — не всегда место жительства; даты включения в перечень у нас есть
только с первого сохранённого снимка.</p>
{cards or empty}
{pager("/ui/unnamed", {"status": status, "person_q": person_q.strip()}, page, pages)}"""
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
            "перечне» оставляет случай открытым для поиска имени."
        ),
        db=db,
    )


def _back_location(back: str, figurant: str) -> str:
    kept = {key: value for key, value in (item.partition("=")[::2] for item in back.split("&"))}
    params = {
        "status": kept.get("status", "open") if kept.get("status") in _STATUSES else "open",
        "page": kept.get("page", "1") if kept.get("page", "").isdigit() else "1",
        "person_q": kept.get("person_q", ""),
    }
    return f"/ui/unnamed?{urlencode(params)}#u-{quote(figurant)}"


async def _form(request: Request) -> dict[str, str]:
    return {
        key: values[0]
        for key, values in parse_qs(
            (await request.body()).decode("utf-8", errors="replace")
        ).items()
    }


def _known(db: Session, figurant: str) -> None:
    known = db.scalar(
        select(func.count())
        .select_from(UnnamedFigurantRecord)
        .where(UnnamedFigurantRecord.key == figurant)
    )
    if not known:
        raise HTTPException(status_code=400, detail="Неизвестный безымянный")


@router.post("/ui/unnamed/reject", response_model=None)
async def reject_unnamed(
    request: Request,
    db: Session = Depends(get_db),  # noqa: B008
) -> RedirectResponse:
    form = await _form(request)
    figurant = form.get("figurant", "")
    _known(db, figurant)
    if not form.get("candidate"):
        raise HTTPException(status_code=400, detail="Не выбран человек из перечня")
    decide(db, figurant, form.get("candidate", ""), DIFFERENT)
    db.commit()
    return RedirectResponse(_back_location(form.get("back", ""), figurant), status_code=303)


@router.post("/ui/unnamed/resolve", response_model=None)
async def resolve_unnamed(
    request: Request,
    db: Session = Depends(get_db),  # noqa: B008
) -> RedirectResponse:
    form = await _form(request)
    figurant = form.get("figurant", "")
    _known(db, figurant)
    resolution = form.get("resolution", "")
    birth = None
    if form.get("rf_birth_date"):
        try:
            birth = date.fromisoformat(form["rf_birth_date"])
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Некорректная дата рождения") from exc
    if resolution == EXISTING_PERSON:
        key = form.get("existing_person_key", "")
        person = db.scalar(select(EntityGroupRecord).where(EntityGroupRecord.key == key))
        if person is None:
            raise HTTPException(status_code=400, detail="Не выбран существующий человек")
        resolve_identity(
            db,
            figurant,
            resolution,
            normalized_name=person.name,
            existing_person_key=person.key,
        )
    elif resolution in (RF_ENTRY, SUPPLIED_NAME, NO_RF_MATCH, INSUFFICIENT):
        try:
            resolve_identity(
                db,
                figurant,
                resolution,
                normalized_name=form.get("normalized_name") or form.get("rf_name") or None,
                rf_name=form.get("rf_name") or None,
                rf_birth_date=birth,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Неполное решение") from exc
    else:
        raise HTTPException(status_code=400, detail="Неполное решение")
    db.commit()
    return RedirectResponse(_back_location(form.get("back", ""), figurant), status_code=303)


@router.post("/ui/unnamed/clear", response_model=None)
async def clear_unnamed(
    request: Request,
    db: Session = Depends(get_db),  # noqa: B008
) -> RedirectResponse:
    form = await _form(request)
    figurant = form.get("figurant", "")
    _known(db, figurant)
    clear_resolution(db, figurant)
    db.commit()
    return RedirectResponse(_back_location(form.get("back", ""), figurant), status_code=303)
