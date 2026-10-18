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
from sqlalchemy.orm import Session

from processing.roles import exclusion_list
from processing.roles.roles import KIND_LABELS
from web.dependencies import get_db
from web.ui.layout import _page

logger = logging.getLogger("web.ui.airtable")

router = APIRouter()

PAGE_SIZE = 100
REFERENCE_URL = "/ui/airtable/officials"
# The categories a row may carry. They are the roles the pipeline already knows, so
# saying "судья" here and "judge" there cannot drift apart into two vocabularies.
CATEGORIES = ("judge", "prosecutor", "police", "official", "lawyer", "witness", "other")


def category_label(value: str) -> str:
    return KIND_LABELS.get(value, "прочее") if value != "other" else "прочее"


# What the list is for, said on the page itself rather than only in the code.
_INTRO = (
    "Люди, которых нельзя делать фигурантом: судьи, прокуроры, следователи, защитники, "
    "свидетели. Человек, которого система сама признала должностным, помечается на его "
    "карточке в «Людях»; здесь — сам список, из которого берётся ответ. "
    "Снимается галочкой, а не удалением: решение остаётся в базе."
)


def officials_listing(db: Session) -> tuple[list[exclusion_list.ListedOfficial], int]:
    """The rows of the list, with the name each one has actually landed on.

    A row that matches no entity yet is shown too, but with a blank name and a mark
    saying so: it is in the list, and the operator should know it does not yet do
    anything.
    """
    return exclusion_list.listing(db, limit=PAGE_SIZE)


def official_suggestions(db: Session) -> list[tuple[str, str, str, str]]:
    """The people the model calls officials and the list does not name: (key, name, kind,
    why the model thinks so)."""
    return [
        (item.key, item.name, item.kind, item.reason)
        for item in exclusion_list.suggestions(db, limit=PAGE_SIZE)
    ]


def _suggestions_html(items: list[tuple[str, str, str, str]]) -> str:
    if not items:
        return ""
    rows = "".join(
        f'<tr><td><a href="/ui/entities/{quote(key)}">{escape(name)}</a></td>'
        f"<td>{escape(category_label(kind))}</td><td>{escape(reason)}</td>"
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


def _rows_html(rows: list[exclusion_list.ListedOfficial]) -> str:
    if not rows:
        return '<tr><td colspan="5" class="muted">Список пуст.</td></tr>'
    cells = []
    for listed in rows:
        row, key, name = listed.official, listed.entity_key, listed.entity_name
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
            f"<td>{escape(category_label(row.category or ''))}</td>"
            f"<td>{escape(row.reason or '—')}</td>"
            f"<td>{'да' if row.active else 'нет'}</td>"
            f"<td>{switch}</td></tr>"
        )
    return "".join(cells)


@router.get("/ui/airtable/officials", response_class=HTMLResponse)
def officials_page(db: Session = Depends(get_db)) -> HTMLResponse:  # noqa: B008
    """The list as it stands, and the form that adds to it."""
    rows, total = officials_listing(db)
    options = "".join(
        f'<option value="{escape(value)}">{escape(category_label(value))}</option>'
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
{_suggestions_html(official_suggestions(db))}
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
    for row in exclusion_list.every_official(db):
        writer.writerow(
            [row.full_name, row.category or "", row.reason or "", "да" if row.active else "нет"]
        )
    return Response(
        content=("﻿" + buffer.getvalue()).encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="dolzhnostnye-lica.csv"'},
    )


def apply_add(db: Session, full_name: str, category: str, reason: str) -> str:
    """Add a person to the list; "added", or "already" for a name it holds. Commits."""
    if category not in CATEGORIES:
        category = "other"
    try:
        status = exclusion_list.add(db, full_name, category, reason)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="ФИО не указано") from exc
    db.commit()
    return status


def apply_add_suggested(db: Session, key: str) -> str:
    """Accept the model's suggestion; "added" or "already". 404 for a key that is no
    suggestion, so the button cannot put an arbitrary name on the list. Commits."""
    status = exclusion_list.add_suggested(db, key)
    if status is None:
        raise HTTPException(status_code=404, detail="Такого предложения нет")
    db.commit()
    return status


def apply_deactivate(db: Session, external_id: str) -> None:
    """Out of force, the row kept; commits."""
    if not exclusion_list.deactivate(db, external_id):
        raise HTTPException(status_code=404, detail="Человека нет в списке")
    db.commit()


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
    status = apply_add(
        db,
        (form.get("full_name") or [""])[0],
        (form.get("category") or ["other"])[0],
        (form.get("reason") or [""])[0],
    )
    return RedirectResponse(f"{REFERENCE_URL}?status={status}", status_code=303)


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
    status = apply_add_suggested(db, (form.get("key") or [""])[0])
    return RedirectResponse(f"{REFERENCE_URL}?status={status}", status_code=303)


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
    apply_deactivate(db, (form.get("external_id") or [""])[0])
    return RedirectResponse(f"{REFERENCE_URL}?status=deactivated", status_code=303)
