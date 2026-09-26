"""Person entities gathered from the publications with a criminal case: a list and a card.

A «статья» on these pages is a Criminal Code article; a news item is a «публикация»."""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import suppress
from datetime import datetime
from html import escape
from typing import Any
from urllib.parse import parse_qs, quote, urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import Text, cast, exists, func, or_, select, text
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from db.orm_models import (
    EntityGroupChargeRecord,
    EntityGroupPoliticsRecord,
    EntityGroupRecord,
    EntityGroupRfMatchRecord,
    EntityGroupRoleRecord,
)
from entities.officials import mark_official
from entities.overrides import MANUAL, correct_name
from entities.politics import CRIMINAL, POLITICAL
from entities.rf_check import FULL
from entities.roles import FIGURANT, KIND_LABELS, MENTIONED, POSSIBLE, UNCLEAR
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
from web.ui.layout import _page, pager
from web.ui.management import (
    _RUN_STATUS_BADGES,
    _RUN_STATUS_LABELS,
    _badge,
    _local_time,
)
from web.ui.pipeline import PipelineState, current_state, deepseek_confirmation, out_of_turn

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
    """Which names a model gave or a person corrected: the rest are the rules' best guess."""
    if name_source == "model":
        return ' <span class="badge" title="Имя в именительном падеже дала модель">ИИ</span>'
    if name_source == MANUAL:
        return ' <span class="badge" title="Имя исправлено вручную">исправлено</span>'
    return ""


_NAME_SOURCES = {"model": "дала модель", MANUAL: "исправлено вручную"}


def _article_order(article: str) -> tuple[int, ...]:
    """«205.2» after «205» and before «207.3»: by number, not as text."""
    return tuple(int(part) if part.isdigit() else 0 for part in article.split("."))


def _political(article: str) -> str:
    return ' <span class="badge">политическая</span>' if article in POLITICAL_ARTICLES else ""


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


def _regions(regions: list[Any], *, short: bool = False) -> str:
    """The regions registry cards give: in the list after the name, on the card in full."""
    if not regions:
        return ""
    names = ", ".join(escape(str(region)) for region, _count in regions)
    return f'<br><span class="muted">{names}</span>' if short else names


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
        return KIND_LABELS.get(kind or "", "задержан или обыскан")
    if role == MENTIONED:
        return f"упомянут: {KIND_LABELS.get(kind or '', 'другое')}"
    return "не ясно"


def _role_mark(found: tuple[str, str | None] | None) -> str:
    if found is None:
        return ""
    role, kind = found
    badge = {FIGURANT: "succeeded", POSSIBLE: "pending"}.get(role, "")
    return f' <span class="badge {badge}">{escape(_role_label(role, kind))}</span>'


_ROLE_METHODS = {
    "article": "статья УК в событии",
    "model": "ответ модели по цитатам",
    "official": "должностное лицо",
    "rules": "правило",
    "manual": "ручная пометка",
}


@router.post("/ui/entities/{key}/name", response_model=None)
async def correct_entity_name(
    key: str,
    request: Request,
    db: Session = Depends(get_db),  # noqa: B008
) -> RedirectResponse:
    """A person's correction of the name, applied at once and kept for every rebuild."""
    form = parse_qs((await request.body()).decode("utf-8", errors="replace"))
    entity = db.scalar(select(EntityGroupRecord).where(EntityGroupRecord.key == key))
    if entity is None:
        raise HTTPException(status_code=404, detail="Сущность не найдена")
    try:
        correct_name(db, entity, (form.get("name") or [""])[0])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Пустое имя") from exc
    db.commit()
    return RedirectResponse(f"/ui/investigations/{quote(key)}", status_code=303)


@router.post("/ui/entities/{key}/official", response_model=None)
async def mark_entity_official(
    key: str,
    request: Request,
    db: Session = Depends(get_db),  # noqa: B008
) -> RedirectResponse:
    """A person's word: this entity is (or is not) an official; applied at once."""
    form = parse_qs((await request.body()).decode("utf-8", errors="replace"))
    entity = db.scalar(select(EntityGroupRecord).where(EntityGroupRecord.key == key))
    if entity is None:
        raise HTTPException(status_code=404, detail="Сущность не найдена")
    mark_official(db, entity, (form.get("official") or [""])[0] == "yes")
    db.commit()
    back = (form.get("back") or [""])[0]
    return RedirectResponse(
        "/ui/officials" if back == "officials" else f"/ui/investigations/{quote(key)}",
        status_code=303,
    )


def _rf_mark(level: str | None) -> str:
    if level == FULL:
        return ' <span class="badge">в перечне</span>'
    if level is not None:
        return ' <span class="badge pending">возможно в перечне</span>'
    return ""


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
        warning = deepseek_confirmation("entities")
        confirm = (
            f" onsubmit=\"return confirm('{escape(warning, quote=True)}')\"" if warning else ""
        )
        return (
            f'<form method="post" action="/ui/entities/collect" class="run-bar"{confirm}>'
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


_ROLE_FILTERS = {
    "figurant": "Фигуранты дел",
    "all": "Все роли",
    POSSIBLE: "Задержан, обыск, административное",
    MENTIONED: "Только упомянуты",
    UNCLEAR: "Роль неясна",
}
_VERDICT_FILTERS = {
    "all": "Любой вердикт",
    POLITICAL: "Политические",
    CRIMINAL: "Обычные уголовные",
    "unclear": "Неясные",
    "none": "Не оценивались",
}
_COLUMNS = (
    ("name", "Имя"),
    ("", "Статьи УК"),
    ("", "События дела"),
    ("mentions", "Упоминаний"),
    ("articles", "Публикаций"),
    ("recent", "Последняя новость"),
    ("", "Как писали"),
)


def _column(key: str, label: str, sort: str, base: dict[str, str]) -> str:
    """A column header; a sortable one is a link, the sorted one says which way."""
    if not key:
        return f'<th scope="col">{label}</th>'
    href = escape(urlencode({**base, "sort": key}), quote=True)
    if key != sort:
        return f'<th scope="col"><a href="/ui/entities?{href}">{label}</a></th>'
    # By name A→Я; by the counts and the date, the most first.
    order, arrow = ("ascending", "▴") if key == "name" else ("descending", "▾")
    return f'<th scope="col" aria-sort="{order}"><a href="/ui/entities?{href}">{label} {arrow}</a></th>'


def _select(name: str, label: str, options: dict[str, str], chosen: str) -> str:
    items = "".join(
        f'<option value="{escape(value, quote=True)}"{" selected" if value == chosen else ""}>'
        f"{escape(text_)}</option>"
        for value, text_ in options.items()
    )
    return (
        f'<label class="field">{escape(label)} '
        f'<select name="{name}" onchange="this.form.submit()">{items}</select></label>'
    )


def _all_regions(db: Session) -> list[str]:
    return list(
        db.scalars(
            text(
                "SELECT DISTINCT region->>0 FROM entity_groups, "
                "jsonb_array_elements(regions) AS region ORDER BY 1"
            )
        )
    )


@router.get("/ui/entities", response_class=HTMLResponse)
def ui_entities(
    q: str = Query(default="", max_length=200),
    article: str = Query(default="", max_length=32),
    # The list confirms who a person is: shown by default, hidden only on request.
    rf: str = Query(default="all", pattern="^(hide|all)$"),
    rf_possible: str = Query(default="all", pattern="^(hide|all)$"),
    # The old «only the figurants» box: `role` says more; kept for the old links.
    figurants: str = Query(default="only", pattern="^(only|all)$"),
    role: str = Query(default="", pattern="^(|figurant|all|possible|mentioned|unclear)$"),
    verdict: str = Query(default="all", pattern="^(all|political|criminal|unclear|none)$"),
    region: str = Query(default="", max_length=200),
    sort: str = Query(default="mentions", pattern="^(mentions|articles|recent|name)$"),
    page: int = Query(default=1, ge=1),
    db: Session = Depends(get_db),  # noqa: B008
    registry: OperationRegistry = Depends(get_operation_registry),  # noqa: B008
) -> HTMLResponse:
    role = role or ("figurant" if figurants == "only" else "all")
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
    region = region.strip()
    if region:
        query = query.where(cast(EntityGroupRecord.regions, Text).ilike(f"%{region}%"))
    if verdict != "all":
        judged = exists().where(EntityGroupPoliticsRecord.group_id == EntityGroupRecord.id)
        query = query.where(
            ~judged
            if verdict == "none"
            else exists().where(
                EntityGroupPoliticsRecord.group_id == EntityGroupRecord.id,
                EntityGroupPoliticsRecord.verdict == verdict,
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
    # Before step 4 has run nobody has a role: the role then filters nothing.
    roles_known = db.scalar(select(exists().select_from(EntityGroupRoleRecord))) or False
    if role != "all" and roles_known:
        query = query.where(
            exists().where(
                EntityGroupRoleRecord.group_id == EntityGroupRecord.id,
                EntityGroupRoleRecord.role == (FIGURANT if role == "figurant" else role),
            )
        )
    # The list's state, kept by every link of the page.
    keep = {
        "rf": rf,
        "rf_possible": rf_possible,
        "role": role,
        "verdict": verdict,
        "region": region,
        "sort": sort,
    }
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
    ]
    roles_note = (
        ""
        if roles_known
        else '<p class="warning">Фигуранты ещё не определены — шаг 4 в '
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
    ids = [entity.id for entity in entities]
    charges, listed, roles = _articles_by_group(db, ids), _rf_levels(db, ids), _roles(db, ids)
    verdicts = _verdicts(db, ids)
    rows = "".join(
        f'<tr data-href="/ui/investigations/{quote(entity.key)}">'
        f'<th scope="row"><a href="/ui/investigations/{quote(entity.key)}">'
        f"{escape(display_name(entity.name))}</a>"
        f"{_source_mark(entity.name_source)}{_rf_mark(listed.get(entity.id))}"
        f"{_role_mark(roles.get(entity.id))}{_verdict_mark(verdicts.get(entity.id))}"
        f"{_regions(entity.regions, short=True)}</th>"
        f"<td>{_article_links(charges.get(entity.id, []), keep)}</td>"
        f"<td>{_events(entity.event_types)}</td>"
        f'<td class="num">{entity.mention_count}</td><td class="num">{entity.article_count}</td>'
        f"<td>{escape(_date(entity.last_published_at))}</td>"
        f'<td class="muted">{escape(", ".join(form for form, _ in entity.variants[:3]))}</td></tr>'
        for entity in entities
    )
    base = {"q": q, "article": article, **keep}
    headers = "".join(_column(key, label, sort, base) for key, label in _COLUMNS)
    pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
    regions = _all_regions(db)
    region_filter = (
        _select("region", "Регион", {"": "Все регионы", **{name: name for name in regions}}, region)
        if regions
        else ""
    )
    table = (
        f"""<table class="sticky-head people"><caption class="visually-hidden">Люди</caption>
<thead><tr>{headers}</tr></thead><tbody>{rows}</tbody></table>"""
        if rows
        else '<p class="empty">Никого не найдено: ослабьте фильтры.</p>'
    )
    body = f"""{_collect_bar(current_state(registry), _last_collect(registry))}
<form method="get" class="toolbar filters" role="search">
  <input type="hidden" name="sort" value="{sort}">
  <label class="field">Имя <input type="search" name="q" value="{
        escape(q, quote=True)
    }" placeholder="Имя или вариант написания"></label>
  <label class="field">Статья УК <input type="search" name="article" value="{
        escape(article, quote=True)
    }" placeholder="напр. 207.3" size="10"></label>
  {_select("role", "Роль", _ROLE_FILTERS, role)}
  {_select("verdict", "Вердикт", _VERDICT_FILTERS, verdict)}
  {region_filter}
  <button type="submit">Найти</button>
  <div class="filter-row">
    <!-- Unticked, only the hidden value is sent; ticked, the box's value comes last and wins. -->
    {"".join(boxes)}
  </div>
</form>
{roles_note}
<p class="muted">Найдено: {total}{found_by}. Одна сущность — одно имя с фамилией в любом падеже.
Серая статья УК — «общая»: в событии обвиняемыми названы и другие люди. «В перечне» — ФИО с
отчеством совпало с перечнем Росфинмониторинга; «возможно в перечне» — совпали имя и фамилия, но
отчества нет с одной из сторон (может быть тёзка).</p>
<section class="band table-band">{table}</section>
{pager("/ui/entities", base, page, pages)}
<script>
// A row opens its dossier; a link or a selection inside it does what it does.
document.querySelectorAll("tr[data-href]").forEach((row) => row.addEventListener("click", (event) => {{
  if (event.target.closest("a") || String(window.getSelection())) return;
  window.location = row.dataset.href;
}}));
</script>"""
    return _page(
        "Люди",
        body,
        active="entities",
        instruction=(
            "Люди из публикаций с уголовными делами, собранные из упоминаний: «Моора», «Моору» и "
            "«Моор» — один человек."
        ),
        next_action="Откройте человека — досье с решением системы, хронологией и доказательствами.",
        db=db,
    )


def _verdicts(db: Session, group_ids: Sequence[int]) -> dict[int, str]:
    return {
        group_id: verdict
        for group_id, verdict in db.execute(
            select(EntityGroupPoliticsRecord.group_id, EntityGroupPoliticsRecord.verdict).where(
                EntityGroupPoliticsRecord.group_id.in_(group_ids)
            )
        ).all()
    }


def _verdict_mark(verdict: str | None) -> str:
    if verdict == POLITICAL:
        return ' <span class="badge succeeded">политическое</span>'
    if verdict == "unclear":
        return ' <span class="badge pending">политичность неясна</span>'
    return ""


@router.post("/ui/entities/collect", response_model=None)
def collect_entities(
    registry: OperationRegistry = Depends(get_operation_registry),  # noqa: B008
) -> RedirectResponse:
    # Out of turn or raced by another start: the page shows why nothing started.
    if out_of_turn(current_state(registry), "entities") is None:
        with suppress(OperationConflictError):
            registry.start("monitor", OperationParameters(mode="entities"))
    return RedirectResponse("/ui/entities", status_code=303)
