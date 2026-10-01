"""The officials list, kept by hand in PostgreSQL.

It is the one reference list that is not read from Airtable: a person is added here
because someone decided they must never become a target figurant, and that decision is
a person's, not a copy. So the console is where it is edited — added, switched off,
looked at, and taken away as a file.

Three rules the code below is built around:

- **Deleting is not removing.** A person is switched off with `active = false`, never
  dropped. The list is a record of what was decided; a row that vanished would take the
  decision with it, and there would be no way to tell «we checked and he is not an
  official» from «nobody ever looked».
- **A name that fits nobody is refused, not written.** If the person is not among the
  entities yet, the row would sit here matching nothing. The answer says so.
- **A name that fits several is refused too.** Two people whose names the same words
  could stand for: excluding the wrong one is worse than excluding neither.

Every write goes through the same check step 4 reads, so what the operator sees on this
page is exactly what the pipeline will act on.

Step 4 puts on the list by itself the officials the texts name by title; the ones only
the model calls officials are not put there — it called an arrested policeman one. They
are offered here, one button each, for a person to accept.
"""

from __future__ import annotations

import csv
import io
import logging
from html import escape
from urllib.parse import parse_qs, quote

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from db.orm_models import EntityGroupRecord, EntityGroupRoleRecord, ExcludedPersonRecord
from entities.officials import OFFICIAL_KINDS, official_entity_ids, official_marks
from entities.roles import KIND_LABELS
from web.dependencies import get_db
from web.ui.layout import _page

logger = logging.getLogger("web.ui.airtable")

router = APIRouter()

PAGE_SIZE = 100
REFERENCE_URL = "/ui/airtable/officials"
# The categories a row may carry. They are the roles the pipeline already knows, so
# saying "судья" here and "judge" there cannot drift apart into two vocabularies.
CATEGORIES = ("judge", "prosecutor", "police", "official", "lawyer", "witness", "other")


def _category_label(value: str) -> str:
    return KIND_LABELS.get(value, "прочее") if value != "other" else "прочее"


# What the list is for, said on the page itself rather than only in the code.
_INTRO = (
    "Люди, которых нельзя делать фигурантом: судьи, прокуроры, следователи, защитники, "
    "свидетели. Человек, которого система сама признала должностным, помечается на его "
    "карточке в «Людях»; здесь — сам список, из которого берётся ответ. "
    "Снимается галочкой, а не удалением: решение остаётся в базе."
)


def _listing(db: Session) -> tuple[list[tuple[ExcludedPersonRecord, str, str]], int]:
    """The rows of the list, with the name each one has actually landed on.

    A row that matches no entity yet is shown too, but with a blank name and a mark
    saying so: it is in the list, and the operator should know it does not yet do
    anything.
    """
    total = db.scalar(select(func.count()).select_from(ExcludedPersonRecord)) or 0
    rows = list(
        db.scalars(
            select(ExcludedPersonRecord)
            .order_by(ExcludedPersonRecord.active.desc(), ExcludedPersonRecord.full_name)
            .limit(PAGE_SIZE)
        )
    )
    groups = list(db.scalars(select(EntityGroupRecord)))
    keys = {group.id: group.key for group in groups}
    names = {group.id: group.name for group in groups}
    landed = official_entity_ids(db, keys)
    # `landed` is keyed by the entity's id. The link needs the key, the person needs the
    # name they are filed under — those differ, since the key is folded.
    found = {id(row): (keys[group_id], names[group_id]) for group_id, row in landed.items()}
    return [(row, *found.get(id(row), ("", ""))) for row in rows], int(total)


def _suggestions(db: Session) -> list[tuple[str, str, str, str]]:
    """The people the model calls officials and the list does not name: (key, name, kind,
    why the model thinks so). Not offered: anyone the list names, active or switched off
    (that was an answer), and anyone with a mark of their own."""
    groups = list(db.scalars(select(EntityGroupRecord)))
    keys = {group.id: group.key for group in groups}
    named = official_entity_ids(db, keys, active_only=False)
    marks = official_marks(db, keys)
    names = {group.id: group.name for group in groups}
    found = []
    for role in db.scalars(
        select(EntityGroupRoleRecord).where(
            EntityGroupRoleRecord.method == "model",
            EntityGroupRoleRecord.kind.in_(sorted(OFFICIAL_KINDS)),
        )
    ):
        if role.group_id in named or role.group_id in marks or role.group_id not in keys:
            continue
        found.append((keys[role.group_id], names[role.group_id], role.kind or "", role.reason))
    return sorted(found, key=lambda item: (item[2], item[1]))[:PAGE_SIZE]


def _suggestions_html(items: list[tuple[str, str, str, str]]) -> str:
    if not items:
        return ""
    rows = "".join(
        f'<tr><td><a href="/ui/entities/{quote(key)}">{escape(name)}</a></td>'
        f"<td>{escape(_category_label(kind))}</td><td>{escape(reason)}</td>"
        '<td><form method="post" action="/ui/airtable/officials/add-suggested">'
        f'<input type="hidden" name="key" value="{escape(key, quote=True)}">'
        '<button type="submit" class="secondary">Добавить в список</button></form></td></tr>'
        for key, name, kind, reason in items
    )
    return f"""<h2>Предложения системы: {len(items)}</h2>
<p class="muted">Модель решила, что эти люди — должностные лица, но перед именем в текстах
должности нет. Это догадка: среди них бывают и обвиняемые, и иностранные политики. Добавьте
тех, кто точно должностное лицо; в силу это вступит при следующем «Найти фигурантов».</p>
<table><thead><tr><th>Кто</th><th>Категория</th><th>Почему модель так думает</th><th></th></tr>
</thead><tbody>{rows}</tbody></table>"""


def _rows_html(rows: list[tuple[ExcludedPersonRecord, str, str]]) -> str:
    if not rows:
        return '<tr><td colspan="5" class="muted">Список пуст.</td></tr>'
    cells = []
    for row, key, name in rows:
        where = (
            f'<a href="/ui/entities/{quote(key)}">{escape(name)}</a>'
            if name
            else '<span class="muted">ещё не встречался в статьях</span>'
        )
        switch = (
            '<form method="post" action="/ui/airtable/officials/deactivate">'
            f'<input type="hidden" name="external_id" value="{escape(row.external_id)}">'
            '<button type="submit" class="secondary">Снять</button></form>'
            if row.active
            else '<span class="muted">снят</span>'
        )
        cells.append(
            f"<tr><td>{escape(row.full_name)}</td><td>{where}</td>"
            f"<td>{escape(_category_label(row.category or ''))}</td>"
            f"<td>{escape(row.reason or '—')}</td>"
            f"<td>{'да' if row.active else 'нет'}</td>"
            f"<td>{switch}</td></tr>"
        )
    return "".join(cells)


@router.get("/ui/airtable/officials", response_class=HTMLResponse)
def officials_page(db: Session = Depends(get_db)) -> HTMLResponse:  # noqa: B008
    """The list as it stands, and the form that adds to it."""
    rows, total = _listing(db)
    options = "".join(
        f'<option value="{escape(value)}">{escape(_category_label(value))}</option>'
        for value in CATEGORIES
    )
    body = f"""<p class="muted">{_INTRO}</p>
<p><a class="button secondary" href="{REFERENCE_URL}.csv">Скачать списком (CSV)</a></p>
<form method="post" action="/ui/airtable/officials/add">
  <fieldset><legend>Добавить человека</legend>
  <p><label>ФИО <input name="full_name" required maxlength="512" size="48"></label></p>
  <p><label>Категория <select name="category">{options}</select></label>
     <label>Причина <input name="reason" maxlength="255" size="40"></label></p>
  <p><button type="submit">Добавить</button></p>
  </fieldset>
</form>
<p class="muted">Всего в списке: {total}. Судей, прокуроров и других, чьё звание стоит в
текстах перед именем, шаг «Найти фигурантов» добавляет сам («автоматически» в причине).</p>
{_suggestions_html(_suggestions(db))}
<table><thead><tr><th>ФИО в списке</th><th>Кого нашли в статьях</th><th>Категория</th>
<th>Причина</th><th>В силе</th><th></th></tr></thead>
<tbody>{_rows_html(rows)}</tbody></table>"""
    return _page("Должностные лица", body, active="airtable", instruction=_INTRO, db=db)


@router.get("/ui/airtable/officials.csv")
def officials_csv(db: Session = Depends(get_db)) -> Response:  # noqa: B008
    """The whole list as a file, all rows and not only the page on screen."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["ФИО", "Категория", "Причина", "В силе"])
    for row in db.scalars(select(ExcludedPersonRecord).order_by(ExcludedPersonRecord.full_name)):
        writer.writerow(
            [row.full_name, row.category or "", row.reason or "", "да" if row.active else "нет"]
        )
    return Response(
        content=("﻿" + buffer.getvalue()).encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="dolzhnostnye-lica.csv"'},
    )


@router.post("/ui/airtable/officials/add", response_model=None)
async def officials_add(
    request: Request,
    db: Session = Depends(get_db),  # noqa: B008
) -> RedirectResponse:
    """Add a person to the list, saying plainly when the name fits nobody.

    A row added for a name that matches no entity is not an error — the articles may not
    have mentioned the person yet — but it is written with a mark saying it does not yet
    apply, so the page does not claim the system will treat them as an official.
    """
    form = parse_qs((await request.body()).decode("utf-8", errors="replace"))
    name = " ".join((form.get("full_name") or [""])[0].split())
    category = (form.get("category") or ["other"])[0]
    reason = (form.get("reason") or [""])[0]
    if not name:
        raise HTTPException(status_code=400, detail="ФИО не указано")
    if category not in CATEGORIES:
        category = "other"
    external_id = f"console:{name.casefold()}"
    existing = db.scalar(
        select(ExcludedPersonRecord).where(ExcludedPersonRecord.external_id == external_id)
    )
    if existing is not None:
        db.commit()
        return RedirectResponse(f"{REFERENCE_URL}?status=already", status_code=303)
    db.add(
        ExcludedPersonRecord(
            external_id=external_id,
            full_name=name,
            normalized_name=" ".join(name.casefold().replace("ё", "е").split()),
            category=category,
            reason=reason.strip() or None,
            active=True,
        )
    )
    db.commit()
    logger.info("event=official_added name=%s category=%s", name, category)
    return RedirectResponse(f"{REFERENCE_URL}?status=added", status_code=303)


@router.post("/ui/airtable/officials/add-suggested", response_model=None)
async def officials_add_suggested(
    request: Request,
    db: Session = Depends(get_db),  # noqa: B008
) -> RedirectResponse:
    """Accept the model's suggestion: the person goes on the list as a person's decision.

    Refused with 404 when the key is no suggestion — no such entity, or the model does not
    call it an official — so the button cannot put an arbitrary name on the list.
    """
    form = parse_qs((await request.body()).decode("utf-8", errors="replace"))
    key = (form.get("key") or [""])[0]
    entity = db.scalar(select(EntityGroupRecord).where(EntityGroupRecord.key == key))
    role = db.get(EntityGroupRoleRecord, entity.id) if entity is not None else None
    if (
        entity is None
        or role is None
        or role.method != "model"
        or (role.kind or "") not in OFFICIAL_KINDS
    ):
        raise HTTPException(status_code=404, detail="Такого предложения нет")
    keys = {group.id: group.key for group in db.scalars(select(EntityGroupRecord))}
    if entity.id in official_entity_ids(db, keys, active_only=False):
        db.commit()
        return RedirectResponse(f"{REFERENCE_URL}?status=already", status_code=303)
    name = " ".join(entity.name.split())
    db.add(
        ExcludedPersonRecord(
            external_id=f"model:{key}",
            full_name=name,
            normalized_name=" ".join(name.casefold().replace("ё", "е").split()),
            category=role.kind or "official",
            reason=f"предложено моделью, принято вручную: {role.reason}".strip(),
            active=True,
        )
    )
    db.commit()
    logger.info("event=official_suggestion_accepted name=%s kind=%s", name, role.kind)
    return RedirectResponse(f"{REFERENCE_URL}?status=added", status_code=303)


@router.post("/ui/airtable/officials/deactivate", response_model=None)
async def officials_deactivate(
    request: Request,
    db: Session = Depends(get_db),  # noqa: B008
) -> RedirectResponse:
    """Take a person out of force without deleting the row.

    The row stays, because «we looked and he is not an official» is an answer, and a
    list that cannot remember it will be asked the same question again.
    """
    form = parse_qs((await request.body()).decode("utf-8", errors="replace"))
    external_id = (form.get("external_id") or [""])[0]
    row = db.scalar(
        select(ExcludedPersonRecord).where(ExcludedPersonRecord.external_id == external_id)
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Человека нет в списке")
    row.active = False
    db.commit()
    logger.info("event=official_deactivated name=%s", row.full_name)
    return RedirectResponse(f"{REFERENCE_URL}?status=deactivated", status_code=303)
