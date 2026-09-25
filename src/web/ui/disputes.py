"""«Спорные случаи»: two entities that may be one person, side by side, for a person to
decide. «Один человек» merges them at once and at every rebuild; «Разные люди» takes
the pair off the list."""

from __future__ import annotations

from html import escape
from typing import Any
from urllib.parse import parse_qs, quote, urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from db.orm_models import EntityGroupRecord
from entities.disputes import (
    DIFFERENT,
    PATRONYMIC,
    SAME,
    SIMILAR,
    EntityRef,
    Pair,
    decide,
    decided_pairs,
    find_pairs,
)
from web.dependencies import get_db
from web.ui.entities import (
    _article_links,
    _articles_by_group,
    _regions,
    _rf_levels,
    _rf_mark,
    _role_mark,
    _roles,
    display_name,
)
from web.ui.layout import _page

router = APIRouter()

PAGE_SIZE = 50
_KINDS = {
    "all": "Все",
    PATRONYMIC: "С отчеством и без",
    SIMILAR: "Похожее имя",
}
_KIND_HINTS = {
    PATRONYMIC: "одно имя и фамилия, у одной сущности есть отчество, у другой нет",
    SIMILAR: "одна фамилия, имена отличаются окончанием («Лида» и «Лидия»)",
}


def _side(
    entity: EntityGroupRecord,
    roles: dict[int, tuple[str, str | None]],
    listed: dict[int, str],
    charges: dict[int, list[tuple[str, bool]]],
) -> str:
    forms = ", ".join(f"{escape(str(form))} ({count})" for form, count in entity.variants[:5])
    articles = _article_links(charges.get(entity.id, []), {"figurants": "all", "rf": "all"})
    return (
        '<div class="pair-side">'
        f'<h3><a href="/ui/entities/{quote(entity.key)}">{escape(display_name(entity.name))}</a>'
        f"{_role_mark(roles.get(entity.id))}{_rf_mark(listed.get(entity.id))}</h3>"
        f'<p class="muted">Как писали: {forms}</p>'
        f"<p>Упоминаний: {entity.mention_count} · публикаций: {entity.article_count}</p>"
        + (f"<p>Регион: {_regions(entity.regions)}</p>" if entity.regions else "")
        + (f"<p>Статьи УК: {articles}</p>" if articles else "")
        + "</div>"
    )


@router.get("/ui/disputes", response_class=HTMLResponse)
def ui_disputes(
    kind: str = Query(default="all", pattern="^(all|patronymic|similar)$"),
    page: int = Query(default=1, ge=1),
    db: Session = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    refs = [
        EntityRef(id=row.id, key=row.key, name=row.name, mention_count=row.mention_count)
        for row in db.execute(
            select(
                EntityGroupRecord.id,
                EntityGroupRecord.key,
                EntityGroupRecord.name,
                EntityGroupRecord.mention_count,
            )
        ).all()
    ]
    pairs = find_pairs(refs, decided_pairs(db))
    counts = {key: sum(pair.kind == key for pair in pairs) for key in (PATRONYMIC, SIMILAR)}
    shown = [pair for pair in pairs if kind == "all" or pair.kind == kind]
    on_page = shown[(page - 1) * PAGE_SIZE : page * PAGE_SIZE]
    ids = [ref.id for pair in on_page for ref in (pair.left, pair.right)]
    records = {
        record.id: record
        for record in db.scalars(select(EntityGroupRecord).where(EntityGroupRecord.id.in_(ids)))
    }
    roles, listed, charges = _roles(db, ids), _rf_levels(db, ids), _articles_by_group(db, ids)
    sections = "".join(
        _pair_section(pair, records, roles, listed, charges, kind, page) for pair in on_page
    )
    chips = " ".join(
        f'<a class="chip{" active" if key == kind else ""}" '
        f'href="/ui/disputes?{urlencode({"kind": key})}">{label}'
        f"{f' ({counts[key]})' if key in counts else f' ({len(pairs)})'}</a>"
        for key, label in _KINDS.items()
    )
    pages = (len(shown) + PAGE_SIZE - 1) // PAGE_SIZE
    pager = " ".join(
        f'<a href="/ui/disputes?{urlencode({"kind": kind, "page": number})}">'
        f"{'<b>' + str(number) + '</b>' if number == page else number}</a>"
        for number in range(1, pages + 1)
    )
    body = f"""<p class="chips">{chips}</p>
<p class="muted">Нерешённых пар: {len(shown)}. «Один человек» сливает две сущности сразу и при
каждой следующей сборке; «Разные люди» убирает пару из списка.</p>
{sections or '<p class="muted">Спорных пар нет.</p>'}
<p class="pager">{pager if pages > 1 else ""}</p>"""
    return _page(
        "Спорные случаи",
        body,
        active="disputes",
        instruction=(
            "Сущности, которые могут быть одним человеком: реестр пишет ФИО с отчеством, "
            "новости — без; имя бывает записано по-разному («Лида» и «Лидия»)."
        ),
        next_action="Сравните две стороны и нажмите «Один человек» или «Разные люди».",
        db=db,
    )


def _pair_section(
    pair: Pair,
    records: dict[int, EntityGroupRecord],
    roles: dict[int, tuple[str, str | None]],
    listed: dict[int, str],
    charges: dict[int, list[tuple[str, bool]]],
    kind: str,
    page: int,
) -> str:
    left, right = records.get(pair.left.id), records.get(pair.right.id)
    if left is None or right is None:
        return ""
    key_a, key_b = pair.keys
    return f"""<section class="band pair" id="pair-{left.id}-{right.id}">
  <p class="muted">{escape(_KIND_HINTS[pair.kind])}</p>
  <div class="pair-sides">{_side(left, roles, listed, charges)}{_side(right, roles, listed, charges)}</div>
  <form method="post" action="/ui/disputes/decide" class="run-bar">
    <input type="hidden" name="key_a" value="{escape(key_a, quote=True)}">
    <input type="hidden" name="key_b" value="{escape(key_b, quote=True)}">
    <input type="hidden" name="kind" value="{kind}">
    <input type="hidden" name="page" value="{page}">
    <button name="decision" value="{SAME}" type="submit">Один человек</button>
    <button name="decision" value="{DIFFERENT}" type="submit" class="secondary">Разные люди</button>
  </form>
</section>"""


@router.post("/ui/disputes/decide", response_model=None)
async def decide_dispute(
    request: Request,
    db: Session = Depends(get_db),  # noqa: B008
) -> RedirectResponse:
    form: dict[str, Any] = {
        key: values[0]
        for key, values in parse_qs(
            (await request.body()).decode("utf-8", errors="replace")
        ).items()
    }
    key_a, key_b = str(form.get("key_a", "")), str(form.get("key_b", ""))
    decision = str(form.get("decision", ""))
    if not key_a or not key_b or key_a == key_b or decision not in (SAME, DIFFERENT):
        raise HTTPException(status_code=400, detail="Неполное решение")
    decide(db, key_a, key_b, decision)
    db.commit()
    kind = form.get("kind") if form.get("kind") in _KINDS else "all"
    page = str(form.get("page", "1"))
    return RedirectResponse(
        f"/ui/disputes?{urlencode({'kind': kind, 'page': page if page.isdigit() else '1'})}",
        status_code=303,
    )
