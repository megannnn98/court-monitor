"""«Спорные случаи»: two entities that may be one person, side by side, for a person to
decide. «Один человек» merges them at once and at every rebuild; «Разные люди» takes
the pair off the list."""

from __future__ import annotations

import json
from html import escape
from typing import Any
from urllib.parse import parse_qs, quote, urlencode

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from processing.identity.service import (
    DIFFERENT,
    SAME,
    Pair,
)
from processing.read import Group
from web.dependencies import get_db
from web.ui.entities import _article_links, _regions, _rf_mark, _role_mark
from web.ui.layout import _page, pager
from web.views.disputes import KIND_HINTS, KINDS, PAGE_SIZE, read_pairs, settle_pair
from web.views.entities import display_name

router = APIRouter()


def _side(
    entity: Group,
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


@router.get("/ui/disputes", response_class=RedirectResponse)
def legacy_disputes(request: Request) -> RedirectResponse:
    query = f"?{request.url.query}" if request.url.query else ""
    return RedirectResponse(f"/ui/pairs{query}", status_code=303)


@router.get("/ui/pairs", response_class=HTMLResponse)
def ui_disputes(
    kind: str = Query(default="all", pattern="^(all|patronymic|name_patronymic|similar|region)$"),
    page: int = Query(default=1, ge=1),
    reset: int | None = Query(default=None, ge=0),
    key: str = Query(default="", max_length=300),
    db: Session = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    data = read_pairs(db, kind=kind, page=page, key=key)
    pairs, counts, notes, shown, on_page = (
        data.pairs,
        data.counts,
        data.notes,
        data.shown,
        data.on_page,
    )
    records, roles, listed, charges = data.records, data.roles, data.listed, data.charges
    sections = "".join(
        _pair_section(pair, records, roles, listed, charges, kind, page, notes.get(pair.keys, ""))
        for pair in on_page
    )
    chips = " ".join(
        f'<a class="chip{" active" if key == kind else ""}" '
        f'href="/ui/pairs?{urlencode({"kind": key})}">{label}'
        f"{f' ({counts[key]})' if key in counts else f' ({len(pairs)})'}</a>"
        for key, label in KINDS.items()
    )
    pages = (len(shown) + PAGE_SIZE - 1) // PAGE_SIZE
    pages_html = pager("/ui/pairs", {"kind": kind}, page, pages)
    decided = data.decided
    total_decided = sum(decided.values())
    confirm = (
        f"Удалить все решения по спорным парам ({total_decided}, из них вручную "
        f"{decided.get('manual', 0)})? Пары «Разные люди» вернутся сразу, слитые люди "
        "разделятся при следующей сборке сущностей. Это необратимо."
    )
    reset_form = (
        f"""<form method="post" action="/ui/pairs/reset-decisions" class="reset-form"
    onsubmit="return confirm({escape(json.dumps(confirm, ensure_ascii=False), quote=True)})">
    <button type="submit" class="danger">Сбросить все решения по парам</button>
    <span class="muted">Сохранено решений: {total_decided} (вручную: {decided.get("manual", 0)},
    по перечню: {decided.get("rf", 0)}, по региону: {decided.get("region", 0)}).</span>
  </form>"""
        if total_decided
        else '<p class="muted">Сохранённых решений по парам нет.</p>'
    )
    reset_notice = (
        f'<p class="warning" role="status">Решения по парам сброшены: {reset}.</p>'
        if reset is not None
        else ""
    )
    body = f"""<p><a href="/ui/cycle">Назад к циклу</a></p>
<h2>Спорные совпадения людей</h2>
<p class="chips">{chips}</p>
<p class="muted">Нерешённых пар: {len(shown)}. «Один человек» сливает две сущности сразу и при
каждой следующей сборке; «Разные люди» убирает пару из списка.</p>
{reset_notice}
{reset_form}
{sections or '<p class="muted">Спорных пар нет.</p>'}
{pages_html}"""
    return _page(
        "Пары",
        body,
        active="disputes",
        instruction=(
            "Сущности, которые могут быть одним человеком: реестр пишет ФИО с отчеством, "
            "новости — без; имя бывает записано по-разному («Лида» и «Лидия»)."
        ),
        db=db,
    )


def _pair_section(
    pair: Pair,
    records: dict[int, Group],
    roles: dict[int, tuple[str, str | None]],
    listed: dict[int, str],
    charges: dict[int, list[tuple[str, bool]]],
    kind: str,
    page: int,
    note: str,
) -> str:
    left, right = records.get(pair.left.id), records.get(pair.right.id)
    if left is None or right is None:
        return ""
    key_a, key_b = pair.keys
    return f"""<section class="band pair" id="pair-{left.id}-{right.id}">
  <p class="muted">{escape(KIND_HINTS[pair.kind])}</p>
  {f'<p class="warning">{escape(note)}</p>' if note else ""}
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
    fields = parse_qs((await request.body()).decode("utf-8", errors="replace"))
    form: dict[str, Any] = {key: values[0] for key, values in fields.items()}
    key_a, key_b = str(form.get("key_a", "")), str(form.get("key_b", ""))
    decision = str(form.get("decision", ""))
    settle_pair(db, key_a, key_b, decision)
    if form.get("back") in {"queue", "pairs"}:
        return RedirectResponse("/ui/pairs", status_code=303)
    kind = form.get("kind") if form.get("kind") in KINDS else "all"
    page = str(form.get("page", "1"))
    return RedirectResponse(
        f"/ui/pairs?{urlencode({'kind': kind, 'page': page if page.isdigit() else '1'})}",
        status_code=303,
    )
