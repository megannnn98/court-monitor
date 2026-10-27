"""Person entities gathered from the publications with a criminal case: a list and a card.

A «статья» on these pages is a Criminal Code article; a news item is a «публикация»."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from contextlib import suppress
from html import escape
from typing import Any
from urllib.parse import parse_qs, quote, urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from analysis.persecution.classifier import POLITICAL_ARTICLES
from analysis.politics.classification import POLITICAL
from analysis.rosfinmonitoring.rf_check import FULL
from application import build_identity_commands, group_followers
from orchestrator.operations import (
    OperationConflictError,
    OperationParameters,
    OperationRegistry,
    OperationRun,
)
from processing.identity.service import MANUAL
from processing.roles.commands import mark_official_of
from processing.roles.roles import FIGURANT, POSSIBLE
from web.dependencies import get_db, get_operation_registry
from web.ui.layout import _page, pager
from web.ui.pipeline import models_query
from web.ui.run_cards import badge
from web.ui.run_tail import tail_html
from web.views.entities import (
    _COLUMNS,
    EVENT_LABELS,
    PAGE_SIZE,
    ROLE_FILTERS,
    VERDICT_FILTERS,
    _date,
    _last_collect,
    display_name,
    read_entity_page,
    regions_text,
    role_label,
)
from web.views.management import StepRefused, shown_models
from web.views.pipeline import (
    PipelineState,
    current_state,
    out_of_turn,
    step_confirmation,
)
from web.views.run_cards import RUN_STATUS_BADGES, RUN_STATUS_LABELS, local_time

router = APIRouter()


def _events(event_types: Mapping[str, int]) -> str:
    return " ".join(
        badge(f"{EVENT_LABELS.get(kind, kind)}: {count}")
        for kind, count in sorted(event_types.items(), key=lambda item: -item[1])
    )


def _source_mark(name_source: str) -> str:
    """Which names a model gave or a person corrected: the rest are the rules' best guess."""
    if name_source == "model":
        return ' <span class="badge" title="Имя в именительном падеже дала модель">ИИ</span>'
    if name_source == MANUAL:
        return ' <span class="badge" title="Имя исправлено вручную">исправлено</span>'
    return ""


def _political(article: str) -> str:
    return ' <span class="badge">политическая</span>' if article in POLITICAL_ARTICLES else ""


def _article_links(articles: list[tuple[str, bool]], keep: dict[str, str]) -> str:
    return ", ".join(
        f'<a href="/ui/entities?{urlencode({"article": article, **keep})}"'
        f"{'' if sole else ' class="muted" title="общая"'}>{escape(article)}</a>"
        for article, sole in articles
    )


def _regions(regions: Sequence[Any], *, short: bool = False) -> str:
    """The regions registry cards give: in the list after the name, on the card in full."""
    if not regions:
        return ""
    names = escape(regions_text(regions))
    return f'<br><span class="muted">{names}</span>' if short else names


def _box(name: str, value: str, ticked: bool, label: str, title: str) -> str:
    """A tick box of the list form: a hidden «all» first, so an unticked box says so."""
    return (
        f'<input type="hidden" name="{name}" value="all">'
        f'<label class="check" title="{escape(title, quote=True)}">'
        f'<input id="box-{name}" type="checkbox" name="{name}" value="{value}"'
        f'{" checked" if ticked else ""} onchange="this.form.submit()"> {escape(label)}</label>'
    )


def _role_mark(found: tuple[str, str | None] | None) -> str:
    if found is None:
        return ""
    role, kind = found
    badge = {FIGURANT: "succeeded", POSSIBLE: "pending"}.get(role, "")
    return f' <span class="badge {badge}">{escape(role_label(role, kind))}</span>'


@router.post("/ui/entities/{key}/name", response_model=None)
async def correct_entity_name(
    key: str,
    request: Request,
    db: Session = Depends(get_db),  # noqa: B008
) -> RedirectResponse:
    """A person's correction of the name, applied at once and kept for every rebuild."""
    form = parse_qs((await request.body()).decode("utf-8", errors="replace"))
    try:
        corrected = build_identity_commands().correct_name(db, key, (form.get("name") or [""])[0])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Пустое имя") from exc
    if corrected is None:
        raise HTTPException(status_code=404, detail="Сущность не найдена")
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
    if not mark_official_of(
        db, key, (form.get("official") or [""])[0] == "yes", downstream=group_followers()
    ):
        raise HTTPException(status_code=404, detail="Сущность не найдена")
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
        f" Последняя сборка: #{last_run.id} {escape(local_time(last_run.created_at))} "
        f"{badge(RUN_STATUS_LABELS[last_run.status], RUN_STATUS_BADGES[last_run.status])}"
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
            f"{tail_html(state.live)}"
            "<script>setTimeout(() => window.location.reload(), 5000);</script>"
        )
    refusal = out_of_turn(state, "entities")
    if refusal is None:
        warning = step_confirmation("entities")
        models = models_query(("entities",))
        query = f"?{models}" if models else ""
        confirm = (
            f" onsubmit=\"return confirm('{escape(warning, quote=True)}')\"" if warning else ""
        )
        return (
            f'<form method="post" action="/ui/entities/collect{query}" class="run-bar"{confirm}>'
            '<button id="collect-button" type="submit">3. Собрать сущности</button>'
            f'<span class="muted">{last}</span></form>'
        )
    return (
        '<div class="run-bar"><button type="button" disabled '
        f'title="{escape(refusal, quote=True)}">3. Собрать сущности</button>'
        f'<span class="muted">{escape(refusal)} <a href="/ui/runs">Журнал запусков</a>.'
        f"{last}</span></div>"
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
    # Why «Собрать сущности» did not start (`collect_entities`): said on the page.
    refused: str = Query(default="", max_length=300),
    db: Session = Depends(get_db),  # noqa: B008
    registry: OperationRegistry = Depends(get_operation_registry),  # noqa: B008
) -> HTMLResponse:
    role = role or ("figurant" if figurants == "only" else "all")
    article = article.strip()
    region = region.strip()
    data = read_entity_page(
        db,
        q=q,
        article=article,
        rf=rf,
        rf_possible=rf_possible,
        role=role,
        verdict=verdict,
        region=region,
        sort=sort,
        page=page,
    )
    hidden, hidden_possible, roles_known = data.hidden, data.hidden_possible, data.roles_known
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
        '<a href="/ui/runs">«Журнале запусков»</a>; пока показаны все.</p>'
    )
    found_by = f" по статье УК {escape(article)}" if article else ""
    total, entities = data.total, data.entities
    charges, listed, roles, verdicts = data.charges, data.listed, data.roles, data.verdicts
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
    regions = data.regions
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
    refusal = f'<p class="warning collect-refused">{escape(refused)}</p>' if refused else ""
    body = f"""{refusal}{_collect_bar(current_state(registry), _last_collect(registry))}
<form method="get" class="toolbar filters" role="search">
  <input type="hidden" name="sort" value="{sort}">
  <label class="field">Имя <input type="search" name="q" value="{
        escape(q, quote=True)
    }" placeholder="Имя или вариант написания"></label>
  <label class="field">Статья УК <input type="search" name="article" value="{
        escape(article, quote=True)
    }" placeholder="напр. 207.3" size="10"></label>
  {_select("role", "Роль", ROLE_FILTERS, role)}
  {_select("verdict", "Вердикт", VERDICT_FILTERS, verdict)}
  {region_filter}
  <button type="submit">Найти</button>
  <div class="filter-row">
    <!-- Unticked, only the hidden value is sent; ticked, the box's value comes last and wins. -->
    {"".join(boxes)}
  </div>
</form>
<p class="export-bar"><a class="button-link" href="/ui/people/export.xlsx">Выгрузить всех
людей в Excel</a> <span class="muted">вся база без фильтров: люди с именем и случаи без
имени</span></p>
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
        "Все люди",
        body,
        active="entities",
        instruction=(
            "Люди из публикаций с уголовными делами, собранные из упоминаний: «Моора», «Моору» и "
            "«Моор» — один человек."
        ),
        db=db,
    )


def _verdict_mark(verdict: str | None) -> str:
    if verdict == POLITICAL:
        return ' <span class="badge succeeded">политическое</span>'
    if verdict == "unclear":
        return ' <span class="badge pending">политичность неясна</span>'
    return ""


@router.post("/ui/entities/collect", response_model=None)
def collect_entities(
    request: Request,
    db: Session = Depends(get_db),  # noqa: B008
    registry: OperationRegistry = Depends(get_operation_registry),  # noqa: B008
) -> RedirectResponse:
    # The models the button's warning showed; 409 when the choice changed since.
    try:
        entity_model, decision_model = shown_models(
            db, request.query_params.get("em"), request.query_params.get("dm")
        )
    except StepRefused as refused:
        # Back to the page, which says why: the other starts answer with a page too.
        return RedirectResponse(f"/ui/entities?refused={quote(refused.message)}", status_code=303)
    # Out of turn or raced by another start: the page shows why nothing started.
    if out_of_turn(current_state(registry), "entities") is None:
        with suppress(OperationConflictError):
            registry.start(
                "monitor",
                OperationParameters(
                    mode="entities", entity_model=entity_model, decision_model=decision_model
                ),
            )
    return RedirectResponse("/ui/entities", status_code=303)
