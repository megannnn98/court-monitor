"""Person entities gathered from the articles with a criminal case: a list and a card."""

from __future__ import annotations

from contextlib import suppress
from html import escape
from typing import Any
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import Text, cast, func, or_, select, text
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from db.orm_models import EntityGroupRecord
from operator_console import (
    OperationConflictError,
    OperationParameters,
    OperationRegistry,
    OperationRun,
)
from web.dependencies import get_db, get_operation_registry
from web.ui.layout import _page
from web.ui.management import (
    _RUN_STATUS_BADGES,
    _RUN_STATUS_LABELS,
    _badge,
    _local_time,
)
from web.ui.pipeline import PipelineState, current_state, out_of_turn

router = APIRouter()

PAGE_SIZE = 100
RELATED_LIMIT = 30
QUOTE_CONTEXT = 160
_EVENT_LABELS = {
    "case_opened": "Возбуждение дела",
    "charge": "Обвинение",
    "arrest": "Арест",
    "detention": "Задержание",
    "search": "Обыск",
    "sentence": "Приговор",
    "fine": "Штраф",
    "release": "Освобождение",
}
_SORTS: dict[str, tuple[ColumnElement[Any], ...]] = {
    "mentions": (EntityGroupRecord.mention_count.desc(), EntityGroupRecord.key.asc()),
    "articles": (EntityGroupRecord.article_count.desc(), EntityGroupRecord.key.asc()),
    "recent": (
        EntityGroupRecord.last_published_at.desc().nulls_last(),
        EntityGroupRecord.key.asc(),
    ),
    "name": (EntityGroupRecord.name.asc(), EntityGroupRecord.key.asc()),
}


def _events(event_types: dict[str, int]) -> str:
    return " ".join(
        _badge(f"{_EVENT_LABELS.get(kind, kind)}: {count}")
        for kind, count in sorted(event_types.items(), key=lambda item: -item[1])
    )


def _source_mark(name_source: str) -> str:
    """Which names a model gave: the rest are the grouping rules' best guess."""
    return (
        ' <span class="badge" title="Имя в именительном падеже дала модель">ИИ</span>'
        if name_source == "model"
        else ""
    )


def _collect_bar(state: PipelineState, last_run: OperationRun | None) -> str:
    """The rebuild button: step 3 of the pipeline, pressable only in its turn."""
    last = (
        f" Последняя сборка: #{last_run.id} {escape(_local_time(last_run.created_at))} "
        f"{_badge(_RUN_STATUS_LABELS[last_run.status], _RUN_STATUS_BADGES[last_run.status])}"
        if last_run is not None
        else " Сущности ещё не собирались."
    )
    if state.live is not None and state.current == "entities":
        return (
            f'<form method="post" action="/ui/management/runs/{state.live.id}/stop" '
            'class="run-bar"><input type="hidden" name="back" value="entities">'
            f'<button id="stop-button" class="danger" type="submit">'
            f"Остановить сборку #{state.live.id}</button>"
            '<span class="muted">Идёт сборка сущностей, страница обновится сама.</span></form>'
            "<script>setTimeout(() => window.location.reload(), 5000);</script>"
        )
    refusal = out_of_turn(state, "entities")
    if refusal is None:
        return (
            '<form method="post" action="/ui/entities/collect" class="run-bar">'
            '<button id="collect-button" type="submit">3. Собрать сущности</button>'
            f'<span class="muted">{last}</span></form>'
        )
    return (
        '<div class="run-bar"><button type="button" disabled '
        f'title="{escape(refusal, quote=True)}">3. Собрать сущности</button>'
        f'<span class="muted">{escape(refusal)} <a href="/ui/management">Управление</a>.'
        f"{last}</span></div>"
    )


def _last_collect(registry: OperationRegistry) -> OperationRun | None:
    return next(
        (run for run in registry.runs_of("monitor", limit=50) if run.parameters.mode == "entities"),
        None,
    )


@router.get("/ui/entities", response_class=HTMLResponse)
def ui_entities(
    q: str = Query(default="", max_length=200),
    sort: str = Query(default="mentions", pattern="^(mentions|articles|recent|name)$"),
    page: int = Query(default=1, ge=1),
    db: Session = Depends(get_db),  # noqa: B008
    registry: OperationRegistry = Depends(get_operation_registry),  # noqa: B008
) -> HTMLResponse:
    query = select(EntityGroupRecord)
    if q.strip():
        pattern = f"%{q.strip()}%"
        query = query.where(
            or_(
                EntityGroupRecord.name.ilike(pattern),
                cast(EntityGroupRecord.variants, Text).ilike(pattern),
            )
        )
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    entities = db.scalars(
        query.order_by(*_SORTS[sort]).offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE)
    ).all()
    rows = "".join(
        f"<tr><td>{position}</td>"
        f'<td><a href="/ui/entities/{quote(entity.key)}">{escape(entity.name)}</a>'
        f"{_source_mark(entity.name_source)}</td>"
        f'<td class="muted">{escape(", ".join(form for form, _ in entity.variants[:3]))}</td>'
        f'<td class="num">{entity.mention_count}</td><td class="num">{entity.article_count}</td>'
        f"<td>{_events(entity.event_types)}</td>"
        f"<td>{escape(_local_time(entity.last_published_at)) if entity.last_published_at else '—'}</td></tr>"
        for position, entity in enumerate(entities, start=(page - 1) * PAGE_SIZE + 1)
    )
    sort_links = " ".join(
        f'<a class="chip{" active" if key == sort else ""}" '
        f'href="/ui/entities?{urlencode({"q": q, "sort": key})}">{label}</a>'
        for key, label in (
            ("mentions", "По упоминаниям"),
            ("articles", "По статьям"),
            ("recent", "По свежести"),
            ("name", "По имени"),
        )
    )
    pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
    pager = " ".join(
        f'<a href="/ui/entities?{urlencode({"q": q, "sort": sort, "page": number})}">'
        f"{'<b>' + str(number) + '</b>' if number == page else number}</a>"
        for number in range(1, pages + 1)
    )
    body = f"""{_collect_bar(current_state(registry), _last_collect(registry))}
<form method="get" class="toolbar">
  <input type="search" name="q" value="{escape(q, quote=True)}" placeholder="Имя или вариант написания">
  <input type="hidden" name="sort" value="{sort}">
  <button>Найти</button>
  <span class="chips">{sort_links}</span>
</form>
<p class="muted">Найдено: {total}. Только люди и только из статей с уголовным делом; одна сущность —
одно имя с фамилией в любом падеже (однофамильцы с тем же именем сливаются).</p>
<table><thead><tr><th>№</th><th>Имя</th><th>Как писали</th><th>Упоминаний</th><th>Статей</th>
<th>События дела</th><th>Последняя новость</th></tr></thead><tbody>{rows}</tbody></table>
<p class="pager">{pager if pages > 1 else ""}</p>"""
    return _page(
        "Сущности",
        body,
        active="entities",
        instruction=(
            "Люди из статей с уголовными делами, собранные из упоминаний: «Моора», «Моору» и "
            "«Моор» — одна сущность."
        ),
        next_action="Откройте сущность: её статьи с цитатами и люди, упомянутые рядом.",
        db=db,
    )


@router.post("/ui/entities/collect", response_model=None)
def collect_entities(
    registry: OperationRegistry = Depends(get_operation_registry),  # noqa: B008
) -> RedirectResponse:
    # Out of turn or raced by another start: the page shows why nothing started.
    if out_of_turn(current_state(registry), "entities") is None:
        with suppress(OperationConflictError):
            registry.start("monitor", OperationParameters(mode="entities"))
    return RedirectResponse("/ui/entities", status_code=303)


_ARTICLES = text(
    """
    SELECT a.id, a.title, a.published_at, m.start_offset, m.end_offset,
           substr(a.text, greatest(m.start_offset - :context, 0) + 1,
                  m.end_offset - greatest(m.start_offset - :context, 0) + :context) AS quote,
           greatest(m.start_offset - :context, 0) AS quote_start
    FROM entity_group_mentions gm
    JOIN entity_mentions m ON m.id = gm.mention_id
    JOIN article_extraction_runs r ON r.id = m.extraction_run_id
    JOIN parsed_articles a ON a.id = r.article_id
    WHERE gm.group_id = :group
    ORDER BY a.published_at DESC NULLS LAST, a.id DESC, m.start_offset
    """
)
_RELATED = text(
    """
    WITH mine AS (
        SELECT DISTINCT r.article_id FROM entity_group_mentions gm
        JOIN entity_mentions m ON m.id = gm.mention_id
        JOIN article_extraction_runs r ON r.id = m.extraction_run_id
        WHERE gm.group_id = :group
    )
    SELECT g.key, g.name, count(DISTINCT r.article_id) AS shared
    FROM entity_group_mentions gm
    JOIN entity_groups g ON g.id = gm.group_id
    JOIN entity_mentions m ON m.id = gm.mention_id
    JOIN article_extraction_runs r ON r.id = m.extraction_run_id
    WHERE r.article_id IN (SELECT article_id FROM mine) AND gm.group_id <> :group
    GROUP BY g.key, g.name ORDER BY shared DESC, g.name LIMIT :limit
    """
)


@router.get("/ui/entities/{key}", response_class=HTMLResponse)
def ui_entity(
    key: str,
    db: Session = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    entity = db.scalar(select(EntityGroupRecord).where(EntityGroupRecord.key == key))
    if entity is None:
        raise HTTPException(status_code=404, detail="Сущность не найдена")
    seen: set[int] = set()
    articles: list[str] = []
    for article_id, title, published_at, start, end, quote_text, quote_start in db.execute(
        _ARTICLES, {"group": entity.id, "context": QUOTE_CONTEXT}
    ).all():
        if article_id in seen:
            continue
        seen.add(article_id)
        # The mention marked inside its surrounding text.
        local_start, local_end = start - quote_start, end - quote_start
        marked = (
            f"…{escape(quote_text[:local_start])}<mark>{escape(quote_text[local_start:local_end])}"
            f"</mark>{escape(quote_text[local_end:])}…"
        )
        articles.append(
            f"<tr><td>{escape(_local_time(published_at)) if published_at else '—'}</td>"
            f'<td><a href="/ui/articles/{article_id}?start={start}&end={end}">{escape(title)}</a></td>'
            f"<td>{marked}</td></tr>"
        )
    related = "".join(
        f'<li><a href="/ui/entities/{quote(other_key)}">{escape(name)}</a> '
        f'<span class="muted">— общих статей: {shared}</span></li>'
        for other_key, name, shared in db.execute(
            _RELATED, {"group": entity.id, "limit": RELATED_LIMIT}
        ).all()
    )
    variants = ", ".join(f"{escape(form)} ({count})" for form, count in entity.variants)
    body = f"""<p><a href="/ui/entities">← Все сущности</a></p>
<section class="band">
  <p><b>Как писали:</b> {variants}</p>
  <p class="muted">Имя: {"дала модель" if entity.name_source == "model" else "по правилам склейки"}{
        {"male": " · мужчина", "female": " · женщина"}.get(entity.gender or "", "")
    }</p>
  <p>Упоминаний: {entity.mention_count} · статей: {entity.article_count} · {
        _events(entity.event_types)
    }</p>
</section>
<section class="band">
  <h2>Связанные люди</h2>
  <p class="muted">Упомянуты в тех же статьях; чем больше общих статей, тем теснее связь.</p>
  <ul>{related or '<li class="muted">Никого рядом.</li>'}</ul>
</section>
<section class="band">
  <h2>Статьи</h2>
  <table><thead><tr><th>Дата</th><th>Статья</th><th>Где упомянут</th></tr></thead>
  <tbody>{"".join(articles)}</tbody></table>
</section>"""
    return _page(
        entity.name,
        body,
        active="entities",
        instruction="Сущность — все упоминания этого имени в статьях с уголовными делами.",
        next_action="Откройте статью: упоминание в ней подсвечено.",
        db=db,
    )
