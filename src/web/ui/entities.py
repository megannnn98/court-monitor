"""Person entities gathered from the publications with a criminal case: a list and a card.

A «статья» on these pages is a Criminal Code article; a news item is a «публикация»."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime
from html import escape
from typing import Any
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import Text, cast, exists, func, or_, select, text
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from db.orm_models import (
    EntityGroupChargeRecord,
    EntityGroupRecord,
    EntityGroupRfMatchRecord,
    EntityGroupRoleRecord,
    RosfinmonitoringEntryRecord,
)
from entities.rf_check import FULL
from entities.roles import FIGURANT, KIND_LABELS, MENTIONED, POSSIBLE
from extraction.name_frequency import lookup_gender
from operator_console import (
    OperationConflictError,
    OperationParameters,
    OperationRegistry,
    OperationRun,
)
from persecution.classifier import POLITICAL_ARTICLES
from web.candidate_rows import _surname_first
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
}


def display_name(name: str) -> str:
    """The name surname first, as the candidates read: «Иванов Иван Петрович».

    An initial stays an initial: «Соломатин П.» is already surname first; «Дмитрий С.»
    (a known given name and a surname's initial) reads «С. Дмитрий»."""
    words = name.split()
    initials = [word for word in words if "." in word]
    if not initials:
        return _surname_first(name)
    full = [word for word in words if "." not in word]
    if len(full) == 1:
        if lookup_gender(full[0]) is not None:
            return " ".join([*initials, full[0]])
        return " ".join([full[0], *initials])
    return " ".join([_surname_first(" ".join(full)), *initials])


def _surname_key(entity: EntityGroupRecord) -> tuple[str, str]:
    return display_name(entity.name).lower().replace("ё", "е"), entity.key


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


def _article_order(article: str) -> tuple[int, ...]:
    """«205.2» after «205» and before «207.3»: by number, not as text."""
    return tuple(int(part) if part.isdigit() else 0 for part in article.split("."))


def _political(article: str) -> str:
    return ' <span class="badge">политическая</span>' if article in POLITICAL_ARTICLES else ""


@dataclass
class _Charge:
    """One Criminal Code article of an entity, with its evidence."""

    article: str
    parts: set[str] = field(default_factory=set)
    events: Counter[str] = field(default_factory=Counter)
    # Some event names this person as its only target; otherwise every event is shared.
    sole: bool = False
    publications: list[str] = field(default_factory=list)


_ENTITY_CHARGES = text(
    """
    SELECT c.article, c.part, c.event_type, c.other_targets, c.quote,
           a.id, a.title, a.published_at
    FROM entity_group_charges c JOIN parsed_articles a ON a.id = c.publication_id
    WHERE c.group_id = :group
    ORDER BY a.published_at DESC NULLS LAST, a.id DESC, c.id
    """
)


def _charges_section(db: Session, group_id: int) -> str:
    charges: dict[str, _Charge] = {}
    for (
        article,
        part,
        event_type,
        others,
        quote_text,
        publication_id,
        title,
        published_at,
    ) in db.execute(_ENTITY_CHARGES, {"group": group_id}).all():
        charge = charges.setdefault(article, _Charge(article))
        if part:
            charge.parts.add(part)
        charge.events[event_type] += 1
        charge.sole = charge.sole or others == 0
        shared = ' <span class="badge">общая</span>' if others else ""
        charge.publications.append(
            f"<tr><td>{escape(_date(published_at))}</td>"
            f'<td><a href="/ui/articles/{publication_id}">{escape(title)}</a>{shared}</td>'
            f"<td>{escape(quote_text)}</td></tr>"
        )
    if not charges:
        return (
            '<section class="band"><h2>Статьи УК</h2><p class="muted">Ни одно событие не '
            "называет статью УК рядом с этим человеком.</p></section>"
        )
    items = []
    for charge in sorted(charges.values(), key=lambda item: _article_order(item.article)):
        parts = ", ".join(f"ч. {part}" for part in sorted(charge.parts, key=_article_order))
        shared = (
            ""
            if charge.sole
            else ' <span class="badge" title="Во всех событиях обвиняемыми названы и другие '
            'люди">общая</span>'
        )
        items.append(
            f"<li><details><summary><b>ст. {escape(charge.article)}</b>"
            f"{f' ({escape(parts)})' if parts else ''}{_political(charge.article)}{shared} "
            f"{_events(dict(charge.events))} "
            f'<span class="muted">публикаций: {len(charge.publications)}</span></summary>'
            "<table><thead><tr><th>Дата</th><th>Публикация</th><th>Событие</th></tr></thead>"
            f"<tbody>{''.join(charge.publications)}</tbody></table></details></li>"
        )
    return f"""<section class="band">
  <h2>Статьи УК</h2>
  <p class="muted">Статья названа основанием события, где этот человек — обвиняемый. «Общая» — в
  событии обвиняемыми названы и другие люди: статья может относиться не к нему.</p>
  <ul class="charges">{"".join(items)}</ul>
</section>"""


def _date(published_at: datetime | None) -> str:
    return _local_time(published_at) if published_at else "—"


def _articles_by_group(db: Session, group_ids: Sequence[int]) -> dict[int, list[tuple[str, bool]]]:
    """Per entity of a page, its articles in number order and whether any is its own."""
    rows = db.execute(
        select(
            EntityGroupChargeRecord.group_id,
            EntityGroupChargeRecord.article,
            func.bool_or(EntityGroupChargeRecord.other_targets == 0),
        )
        .where(EntityGroupChargeRecord.group_id.in_(group_ids))
        .group_by(EntityGroupChargeRecord.group_id, EntityGroupChargeRecord.article)
    ).all()
    articles: dict[int, list[tuple[str, bool]]] = {}
    for group_id, article, sole in rows:
        articles.setdefault(group_id, []).append((article, bool(sole)))
    for found in articles.values():
        found.sort(key=lambda item: _article_order(item[0]))
    return articles


def _article_links(articles: list[tuple[str, bool]], keep: dict[str, str]) -> str:
    return ", ".join(
        f'<a href="/ui/entities?{urlencode({"article": article, **keep})}"'
        f"{'' if sole else ' class="muted" title="общая"'}>{escape(article)}</a>"
        for article, sole in articles
    )


def _rf_levels(db: Session, group_ids: Sequence[int]) -> dict[int, str]:
    """Per entity of a page, its strongest Rosfinmonitoring match: full, else name."""
    levels: dict[int, str] = {}
    for group_id, level in db.execute(
        select(EntityGroupRfMatchRecord.group_id, EntityGroupRfMatchRecord.level)
        .where(EntityGroupRfMatchRecord.group_id.in_(group_ids))
        .distinct()
    ).all():
        if levels.get(group_id) != FULL:
            levels[group_id] = level
    return levels


def _box(name: str, value: str, ticked: bool, label: str, title: str) -> str:
    """A tick box of the list form: a hidden «all» first, so an unticked box says so."""
    return (
        f'<input type="hidden" name="{name}" value="all">'
        f'<label class="check" title="{escape(title, quote=True)}">'
        f'<input id="box-{name}" type="checkbox" name="{name}" value="{value}"'
        f'{" checked" if ticked else ""} onchange="this.form.submit()"> {escape(label)}</label>'
    )


def _roles(db: Session, group_ids: Sequence[int]) -> dict[int, tuple[str, str | None]]:
    return {
        group_id: (role, kind)
        for group_id, role, kind in db.execute(
            select(
                EntityGroupRoleRecord.group_id,
                EntityGroupRoleRecord.role,
                EntityGroupRoleRecord.kind,
            ).where(EntityGroupRoleRecord.group_id.in_(group_ids))
        ).all()
    }


def _role_label(role: str, kind: str | None) -> str:
    if role == FIGURANT:
        return "фигурант дела"
    if role == POSSIBLE:
        return "задержан или обыскан"
    if role == MENTIONED:
        return f"упомянут: {KIND_LABELS.get(kind or '', 'другое')}"
    return "не ясно"


def _role_mark(found: tuple[str, str | None] | None) -> str:
    if found is None:
        return ""
    role, kind = found
    badge = {FIGURANT: "succeeded", POSSIBLE: "pending"}.get(role, "")
    return f' <span class="badge {badge}">{escape(_role_label(role, kind))}</span>'


def _role_section(db: Session, group_id: int) -> str:
    record = db.get(EntityGroupRoleRecord, group_id)
    if record is None:
        return ""
    method = "статья УК в событии" if record.method == "article" else "ответ модели по цитатам"
    return f"""<section class="band">
  <h2>Роль в деле</h2>
  <p>{_role_mark((record.role, record.kind))} <span class="muted">— {method}</span></p>
  <p>{escape(record.reason)}</p>
  {f'<blockquote class="muted">{escape(record.quote)}</blockquote>' if record.quote else ""}
</section>"""


def _rf_mark(level: str | None) -> str:
    if level == FULL:
        return ' <span class="badge failed">в перечне</span>'
    if level is not None:
        return ' <span class="badge pending">возможно в перечне</span>'
    return ""


def _rf_section(db: Session, group_id: int) -> str:
    rows = db.execute(
        select(
            EntityGroupRfMatchRecord.level,
            RosfinmonitoringEntryRecord.full_name,
            RosfinmonitoringEntryRecord.birth_date,
            RosfinmonitoringEntryRecord.birth_place,
        )
        .join(
            RosfinmonitoringEntryRecord,
            RosfinmonitoringEntryRecord.id == EntityGroupRfMatchRecord.entry_id,
        )
        .where(EntityGroupRfMatchRecord.group_id == group_id)
        .order_by(EntityGroupRfMatchRecord.level, RosfinmonitoringEntryRecord.full_name)
    ).all()
    if not rows:
        return ""
    items = "".join(
        f"<li>{_rf_mark(level)} {escape(full_name)}"
        f"{f', {birth_date:%d.%m.%Y} г.р.' if birth_date else ''}"
        f"{f', {escape(birth_place)}' if birth_place else ''}</li>"
        for level, full_name, birth_date, birth_place in rows
    )
    return f"""<section class="band">
  <h2>Росфинмониторинг</h2>
  <p class="muted">Совпадение по имени: даты рождения в новостях нет, поэтому тёзку отличает
  только отчество.</p>
  <ul>{items}</ul>
</section>"""


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
    article: str = Query(default="", max_length=32),
    rf: str = Query(default="hide", pattern="^(hide|all)$"),
    rf_possible: str = Query(default="all", pattern="^(hide|all)$"),
    figurants: str = Query(default="only", pattern="^(only|all)$"),
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
    article = article.strip()
    if article:
        query = query.where(
            exists().where(
                EntityGroupChargeRecord.group_id == EntityGroupRecord.id,
                EntityGroupChargeRecord.article == article,
            )
        )
    in_list = exists().where(
        EntityGroupRfMatchRecord.group_id == EntityGroupRecord.id,
        EntityGroupRfMatchRecord.level == FULL,
    )
    # Given name and surname only: not on the list for certain, maybe a namesake.
    maybe_listed = ~in_list & exists().where(
        EntityGroupRfMatchRecord.group_id == EntityGroupRecord.id,
        EntityGroupRfMatchRecord.level != FULL,
    )
    hidden = db.scalar(select(func.count()).select_from(query.where(in_list).subquery())) or 0
    hidden_possible = (
        db.scalar(select(func.count()).select_from(query.where(maybe_listed).subquery())) or 0
    )
    if rf == "hide":
        query = query.where(~in_list)
    if rf_possible == "hide":
        query = query.where(~maybe_listed)
    # Before step 5 has run nobody has a role: the box then filters nothing.
    roles_known = db.scalar(select(exists().select_from(EntityGroupRoleRecord))) or False
    is_figurant = exists().where(
        EntityGroupRoleRecord.group_id == EntityGroupRecord.id,
        EntityGroupRoleRecord.role == FIGURANT,
    )
    figurant_count = db.scalar(
        select(func.count()).select_from(query.where(is_figurant).subquery())
    )
    if figurants == "only" and roles_known:
        query = query.where(is_figurant)
    # The list's state, kept by every link of the page.
    keep = {"rf": rf, "rf_possible": rf_possible, "figurants": figurants, "sort": sort}
    boxes = [
        _box(
            "rf",
            "hide",
            rf == "hide",
            f"Скрыть тех, кто в перечне РФМ ({hidden})",
            "ФИО с отчеством совпало с перечнем Росфинмониторинга",
        ),
        _box(
            "rf_possible",
            "hide",
            rf_possible == "hide",
            f"Скрыть возможных — тёзки без отчества ({hidden_possible})",
            "Совпали имя и фамилия, отчества нет с одной из сторон: может быть тёзка",
        ),
        _box(
            "figurants",
            "only",
            figurants == "only",
            f"Только фигуранты дел ({figurant_count or 0})",
            "На человека заведено уголовное дело: по статье УК в событии или по ответу модели",
        ),
    ]
    roles_note = (
        ""
        if roles_known
        else '<p class="warning">Фигуранты ещё не определены — шаг 5 в '
        '<a href="/ui/management">«Управлении»</a>; пока показаны все.</p>'
    )
    found_by = f" по статье УК {escape(article)}" if article else ""
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    if sort == "name":
        # By the name as shown: the surname is not a column, and ten thousand short rows
        # sort here in milliseconds.
        ordered = sorted(db.scalars(query).all(), key=_surname_key)
        entities = ordered[(page - 1) * PAGE_SIZE : page * PAGE_SIZE]
    else:
        entities = list(
            db.scalars(
                query.order_by(*_SORTS[sort]).offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE)
            ).all()
        )
    charges = _articles_by_group(db, [entity.id for entity in entities])
    listed = _rf_levels(db, [entity.id for entity in entities])
    roles = _roles(db, [entity.id for entity in entities])
    rows = "".join(
        f"<tr><td>{position}</td>"
        f'<td><a href="/ui/entities/{quote(entity.key)}">{escape(display_name(entity.name))}</a>'
        f"{_source_mark(entity.name_source)}{_rf_mark(listed.get(entity.id))}"
        f"{_role_mark(roles.get(entity.id))}</td>"
        f'<td class="muted">{escape(", ".join(form for form, _ in entity.variants[:3]))}</td>'
        f'<td class="num">{entity.mention_count}</td><td class="num">{entity.article_count}</td>'
        f"<td>{_article_links(charges.get(entity.id, []), keep)}</td>"
        f"<td>{_events(entity.event_types)}</td>"
        f"<td>{escape(_date(entity.last_published_at))}</td></tr>"
        for position, entity in enumerate(entities, start=(page - 1) * PAGE_SIZE + 1)
    )
    sort_links = " ".join(
        f'<a class="chip{" active" if key == sort else ""}" '
        f'href="/ui/entities?{urlencode({"q": q, "article": article, **keep, "sort": key})}">{label}</a>'
        for key, label in (
            ("mentions", "По упоминаниям"),
            ("articles", "По публикациям"),
            ("recent", "По свежести"),
            ("name", "По фамилии"),
        )
    )
    pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
    pager = " ".join(
        f'<a href="/ui/entities?{urlencode({"q": q, "article": article, **keep, "page": number})}">'
        f"{'<b>' + str(number) + '</b>' if number == page else number}</a>"
        for number in range(1, pages + 1)
    )
    body = f"""{_collect_bar(current_state(registry), _last_collect(registry))}
<form method="get" class="toolbar">
  <input type="search" name="q" value="{
        escape(q, quote=True)
    }" placeholder="Имя или вариант написания">
  <input type="hidden" name="sort" value="{sort}">
  <input type="search" name="article" value="{
        escape(article, quote=True)
    }" placeholder="Статья УК, напр. 207.3" size="18">
  <button>Найти</button>
  <span class="chips">{sort_links}</span>
  <div class="filter-row">
    <!-- Unticked, only the hidden value is sent; ticked, the box's value comes last and wins. -->
    {"".join(boxes)}
  </div>
</form>
{roles_note}
<p class="muted">Найдено: {total}{found_by}. Только люди и только из
публикаций с уголовным делом; одна сущность — одно имя с фамилией в любом падеже (однофамильцы
с тем же именем сливаются). Серая статья УК — «общая»: в событии обвиняемыми названы и другие люди.
«В перечне» — ФИО с отчеством совпало с перечнем Росфинмониторинга; «возможно в перечне» — совпали
имя и фамилия, но отчества нет с одной из сторон (может быть тёзка).</p>
<table><thead><tr><th>№</th><th>Имя</th><th>Как писали</th><th>Упоминаний</th><th>Публикаций</th>
<th>Статьи УК</th><th>События дела</th><th>Последняя новость</th></tr></thead><tbody>{
        rows
    }</tbody></table>
<p class="pager">{pager if pages > 1 else ""}</p>"""
    return _page(
        "Сущности",
        body,
        active="entities",
        instruction=(
            "Люди из публикаций с уголовными делами, собранные из упоминаний: «Моора», «Моору» и "
            "«Моор» — одна сущность."
        ),
        next_action="Откройте сущность: её статьи УК, публикации с цитатами и люди рядом.",
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
        f'<li><a href="/ui/entities/{quote(other_key)}">{escape(display_name(name))}</a> '
        f'<span class="muted">— общих публикаций: {shared}</span></li>'
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
  <p>Упоминаний: {entity.mention_count} · публикаций: {entity.article_count} · {
        _events(entity.event_types)
    }</p>
</section>
<section class="band">
  <h2>Связанные люди</h2>
  <p class="muted">Упомянуты в тех же публикациях; чем больше общих, тем теснее связь.</p>
  <ul>{related or '<li class="muted">Никого рядом.</li>'}</ul>
</section>
{_role_section(db, entity.id)}
{_rf_section(db, entity.id)}
{_charges_section(db, entity.id)}
<section class="band">
  <h2>Публикации</h2>
  <table><thead><tr><th>Дата</th><th>Публикация</th><th>Где упомянут</th></tr></thead>
  <tbody>{"".join(articles)}</tbody></table>
</section>"""
    return _page(
        display_name(entity.name),
        body,
        active="entities",
        instruction="Сущность — все упоминания этого имени в публикациях с уголовными делами.",
        next_action="Раскройте статью УК — публикации, где она названа; откройте публикацию — упоминание подсвечено.",
        db=db,
    )
