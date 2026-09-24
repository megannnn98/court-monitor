"""Temporary news-source selection and manual pipeline runs."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from html import escape
from typing import Literal
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from db.orm_models import ParsedArticleRecord, Source, SourceDocument
from monitoring.models import MonitoringRunStatus, MonitoringRunView, MonitoringTrigger
from monitoring.repository import SqlAlchemyMonitoringRepository
from operator_console import (
    OperationConflictError,
    OperationNotFoundError,
    OperationParameters,
    OperationRegistry,
    OperationRun,
    OperationRunStatus,
)
from sources.source_registry import SourceDefinition, news_sources
from web.dependencies import get_db, get_operation_registry, session_factory_for
from web.ui.layout import _page

router = APIRouter()

_OPERATION = "monitor"
_RUN_STATUS_LABELS = {
    OperationRunStatus.PENDING: "В очереди",
    OperationRunStatus.RUNNING: "Выполняется",
    OperationRunStatus.SUCCEEDED: "Завершено",
    OperationRunStatus.FAILED: "Завершено с ошибками",
    OperationRunStatus.INTERRUPTED: "Прервано",
}
_MONITORING_STATUS_LABELS = {
    MonitoringRunStatus.RUNNING: "Выполняется",
    MonitoringRunStatus.COMPLETED: "Завершено",
    MonitoringRunStatus.COMPLETED_WITH_ERRORS: "Завершено с ошибками",
    MonitoringRunStatus.FAILED: "Ошибка",
    MonitoringRunStatus.ABORTED: "Прервано",
}
# Badge colours of `local-ui.css`: yellow while in progress or partly done, green, red.
_RUN_STATUS_BADGES = {
    OperationRunStatus.PENDING: "pending",
    OperationRunStatus.RUNNING: "running",
    OperationRunStatus.SUCCEEDED: "succeeded",
    OperationRunStatus.FAILED: "failed",
    OperationRunStatus.INTERRUPTED: "failed",
}
_MONITORING_STATUS_BADGES = {
    MonitoringRunStatus.RUNNING: "running",
    MonitoringRunStatus.COMPLETED: "succeeded",
    MonitoringRunStatus.COMPLETED_WITH_ERRORS: "partial",
    MonitoringRunStatus.FAILED: "failed",
    MonitoringRunStatus.ABORTED: "failed",
}
# A source that has not loaded successfully for this long is worth a look.
STALE_AFTER = timedelta(days=7)
HISTORY_SIZE = 10
# Only a display filter: court press services are recognised by their name.
_COURT_NAME = re.compile(r"суд|фемид", re.IGNORECASE)
_SOURCE_KINDS = {"telegram": "Telegram", "court": "Суд", "site": "Сайт"}


def _stop_form(run: OperationRun, back: str) -> str:
    """A button that stops a live run; `back` is the page to return to."""
    return (
        f'<form method="post" action="/ui/management/runs/{run.id}/stop" class="stop-form" '
        "onsubmit=\"return confirm('Остановить загрузку? Уже загруженное останется в базе.')\">"
        f'<input type="hidden" name="back" value="{escape(back, quote=True)}">'
        '<button type="submit" class="danger">Остановить</button></form>'
    )


def _badge(label: str, css_class: str = "") -> str:
    return f'<span class="badge {css_class}">{escape(label)}</span>'


def _local_time(moment: datetime) -> str:
    return moment.astimezone().strftime("%d.%m.%Y %H:%M")


def _source_kind(definition: SourceDefinition) -> str:
    if _COURT_NAME.search(definition.source_name):
        return "court"
    return "telegram" if definition.base_url.startswith("https://t.me/") else "site"


def _source_rows(db: Session, definitions: Sequence[SourceDefinition], selected: set[str]) -> str:
    """One table row per news source: when a document of it was last fetched, and its articles.

    The fetch time comes from the documents themselves, not from the monitoring checkpoint:
    catch-up and backfill loads never write the checkpoint, so it would call a source that
    loads every day "never loaded"."""
    loads = {
        str(base_url): (int(count), fetched_at)
        for base_url, count, fetched_at in db.execute(
            select(
                Source.base_url,
                func.count(ParsedArticleRecord.id),
                func.max(SourceDocument.fetched_at),
            )
            .join(SourceDocument, SourceDocument.source_id == Source.id)
            .outerjoin(ParsedArticleRecord, ParsedArticleRecord.document_id == SourceDocument.id)
            .group_by(Source.base_url)
        ).all()
    }
    now = datetime.now(UTC)
    rows: list[str] = []
    for item in definitions:
        articles, fetched_at = loads.get(item.base_url, (0, None))
        if fetched_at is None:
            loaded = '<td class="never">никогда</td>'
        else:
            freshness = "stale" if now - fetched_at > STALE_AFTER else ""
            loaded = f'<td class="{freshness}">{escape(_local_time(fetched_at))}</td>'
        kind = _source_kind(item)
        search = f"{item.source_name} {item.name}".lower()
        rows.append(
            f'<tr data-kind="{kind}" data-search="{escape(search, quote=True)}">'
            f'<td class="pick"><input type="checkbox" name="sources" '
            f'value="{escape(item.name, quote=True)}" '
            f"{'checked' if item.name in selected else ''}></td>"
            f"<td>{escape(item.source_name)}</td>"
            f"<td><code>{escape(item.name)}</code></td>"
            f"<td>{_SOURCE_KINDS[kind]}</td>"
            f"{loaded}"
            f'<td class="num">{articles}</td>'
            "</tr>"
        )
    return "".join(rows)


def _filter_chips(definitions: Sequence[SourceDefinition]) -> str:
    counts = {kind: 0 for kind in _SOURCE_KINDS}
    for item in definitions:
        counts[_source_kind(item)] += 1
    chips = [
        f'<button type="button" class="chip active" data-kind="all">Все ({len(definitions)})</button>'
    ]
    chips += [
        f'<button type="button" class="chip" data-kind="{kind}">{label} ({counts[kind]})</button>'
        for kind, label in _SOURCE_KINDS.items()
        if counts[kind]
    ]
    return "".join(chips)


_MODE_TITLES = {
    "load": "Загрузка статей",
    "resolve": "Разрешение персон",
    None: "Загрузка и разрешение",
}


def _has_derived_step(run: OperationRun) -> bool:
    """A load leaves classification to the resolution run; the other runs end with it."""
    return run.parameters.mode != "load"


def _resolution_progress(item: MonitoringRunView) -> tuple[int, int] | None:
    """(done, total) extraction runs of a source's resolution stage, while it is recorded."""
    metrics = item.stage_metrics.get("resolution")
    if not isinstance(metrics, dict) or "extraction_runs" not in metrics:
        return None
    total = int(metrics["extraction_runs"])
    return int(metrics.get("done", total)), total


def _history(runs: Sequence[OperationRun], current: OperationRun | None) -> str:
    if not runs:
        return ""
    rows = "".join(
        f'<tr class="{"current" if current is not None and run.id == current.id else ""}">'
        f'<td><a href="/ui/management?run_id={run.id}">#{run.id}</a></td>'
        f"<td>{_MODE_TITLES[run.parameters.mode]}</td>"
        f"<td>{escape(_local_time(run.created_at))}</td>"
        f"<td>{_badge(_RUN_STATUS_LABELS[run.status], _RUN_STATUS_BADGES[run.status])}</td>"
        f'<td class="num">{len(run.parameters.sources or [])}</td>'
        "</tr>"
        for run in runs
    )
    return f"""<section class="band">
  <h2>Последние ручные запуски</h2>
  <table><thead><tr><th>Запуск</th><th>Что</th><th>Начат</th><th>Статус</th><th>Источников</th></tr></thead>
  <tbody>{rows}</tbody></table>
</section>"""


def _skipped_sources(run: OperationRun) -> dict[str, str]:
    try:
        results = json.loads(run.stdout)
    except json.JSONDecodeError:
        return {}
    if not isinstance(results, list):
        return {}
    return {
        str(item["source"]): str(item["skipped"])
        for item in results
        if isinstance(item, dict) and item.get("source") and item.get("skipped")
    }


def _monitoring_runs(db: Session, run: OperationRun) -> list[MonitoringRunView]:
    """The monitoring runs of one manual run: one per source, then the shared derived one."""
    return SqlAlchemyMonitoringRepository(session_factory_for(db)).list_runs_started_between(
        run.started_at or run.created_at,
        run.finished_at,
        trigger=MonitoringTrigger.MANUAL,
    )


def _duration(seconds: float) -> str:
    minutes, seconds = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours} ч {minutes:02d} мин" if hours else f"{minutes} мин {seconds:02d} с"


def _progress(db: Session, run: OperationRun) -> str:
    """Where a live manual run is: which source of how many, and how far into it.

    Built from the counters monitoring writes as it goes; empty once the run ended."""
    if run.status not in (OperationRunStatus.PENDING, OperationRunStatus.RUNNING):
        return ""
    selected = run.parameters.sources or []
    monitoring_runs = _monitoring_runs(db, run)
    per_source = {item.source: item for item in monitoring_runs if item.source in selected}
    derived = next((item for item in monitoring_runs if item.source is None), None)
    running = MonitoringRunStatus.RUNNING
    steps = len(selected) + _has_derived_step(run)
    done = sum(item.status is not running for item in per_source.values())
    names = {item.name: item.source_name for item in news_sources()}
    current = next((item for item in per_source.values() if item.status is running), None)
    detail = ""
    if run.status is OperationRunStatus.PENDING or run.started_at is None:
        step = "Запуск готовится…"
    elif derived is not None:
        done = len(selected) + (derived.status is not running)
        step = f"Шаг {steps} из {steps}: общая классификация и сверка с РФМ"
    elif current is not None and current.source is not None:
        step = (
            f"Источник {done + 1} из {len(selected)}: "
            f"{escape(names.get(current.source, current.source))}"
        )
        handled = current.documents_skipped + current.documents_ingested + current.documents_failed
        resolution = _resolution_progress(current)
        if resolution is not None:
            resolved, total = resolution
            detail = (
                f'<progress class="step" value="{resolved}" max="{max(total, 1)}"></progress>'
                f'<p class="muted">Разрешение персон: статей {resolved} из {total}; '
                f"новых персон {current.persons_created}, привязано {current.persons_linked}, "
                f"на ревью {current.person_reviews_created}</p>"
            )
        elif run.parameters.mode == "resolve":
            detail = '<p class="muted">Ищу статьи с неразобранными упоминаниями…</p>'
        elif current.documents_discovered == 0:
            detail = '<p class="muted">Ищу новые публикации…</p>'
        elif handled < current.documents_discovered:
            detail = (
                f'<progress class="step" value="{handled}" max="{current.documents_discovered}">'
                "</progress>"
                f'<p class="muted">Документов {handled} из {current.documents_discovered}: '
                f"загружено {current.documents_ingested}, уже были {current.documents_skipped}, "
                f"ошибок {current.documents_failed}</p>"
            )
        else:
            detail = (
                f'<p class="muted">Документы загружены ({current.documents_ingested} новых), '
                "извлекаю людей и события…</p>"
            )
    else:
        step = f"Перехожу к следующему источнику ({done} из {len(selected)} готово)…"
    started = run.started_at or run.created_at
    elapsed = _duration((datetime.now(UTC) - started).total_seconds())
    return f"""<div class="progress-box">
  <progress class="overall" value="{done}" max="{steps}"></progress>
  <p><strong>{step}</strong> <span class="muted">· готово {done} из {steps} шагов · идёт {elapsed}</span></p>
  {detail}
</div>"""


def _run_results(db: Session, run: OperationRun, selected: Sequence[str]) -> str:
    monitoring_runs = _monitoring_runs(db, run)
    per_source = {item.source: item for item in monitoring_runs if item.source is not None}
    derived = next((item for item in monitoring_runs if item.source is None), None)
    skipped = _skipped_sources(run)

    names = {item.name: item.source_name for item in news_sources()}
    in_progress = run.status in (OperationRunStatus.PENDING, OperationRunStatus.RUNNING)
    resolving = run.parameters.mode == "resolve"
    rows: list[str] = []
    for source in selected:
        title = (
            f"{escape(names.get(source, source))}<br><code>{escape(source)}</code>"
            if source in names
            else f"<code>{escape(source)}</code>"
        )
        item = per_source.get(source)
        if item is not None:
            resolution = _resolution_progress(item)
            values = (
                (
                    resolution[0] if resolution else 0,
                    item.persons_created,
                    item.persons_linked,
                    item.person_reviews_created,
                    item.error_count,
                )
                if resolving
                else (
                    item.documents_discovered,
                    item.documents_ingested,
                    item.articles_extracted,
                    item.events_created,
                    item.error_count,
                )
            )
            numbers = "".join(f'<td class="num">{value}</td>' for value in values)
            message = escape(item.error_message[:240]) if item.error_message else ""
            rows.append(
                f"<tr><td>{title}</td>"
                f"<td>{_badge(_MONITORING_STATUS_LABELS[item.status], _MONITORING_STATUS_BADGES[item.status])}</td>"
                f'{numbers}<td class="error-text">{message}</td></tr>'
            )
            continue
        if skipped.get(source) == "already_running":
            status = _badge("Уже выполняется другим запуском", "partial")
        elif in_progress:
            status = _badge("Ожидает запуска", "pending")
        else:
            status = _badge("Не запускался")
        rows.append(f'<tr><td>{title}</td><td>{status}</td><td colspan="6"></td></tr>')

    if _has_derived_step(run):
        if derived is not None:
            derived_status = _badge(
                _MONITORING_STATUS_LABELS[derived.status],
                _MONITORING_STATUS_BADGES[derived.status],
            )
            derived_metrics = (
                f"Классифицировано: {derived.classifications_created}; совпадений РФМ: "
                f"{derived.rf_matches_created}; ошибок: {derived.error_count}"
            )
        else:
            derived_status = _badge("Ожидает", "pending") if in_progress else _badge("Не запущен")
            derived_metrics = ""
        rows.append(
            f'<tr class="derived"><td>Общая классификация и сверка с РФМ</td>'
            f'<td>{derived_status}</td><td colspan="6">{derived_metrics}</td></tr>'
        )
    columns = (
        "<th>Статей разобрано</th><th>Новых персон</th><th>Привязано</th><th>На ревью</th>"
        if resolving
        else "<th>Найдено</th><th>Загружено</th><th>Статей</th><th>Событий</th>"
    )
    overall = _RUN_STATUS_LABELS[run.status]
    overall_badge = _RUN_STATUS_BADGES[run.status]
    if run.status is OperationRunStatus.FAILED and any(
        item.status in (MonitoringRunStatus.COMPLETED, MonitoringRunStatus.COMPLETED_WITH_ERRORS)
        for item in monitoring_runs
    ):
        overall, overall_badge = "Завершено частично: есть ошибки", "partial"
    refresh = (
        '<p class="muted">Страница обновляется автоматически. '
        f'<a href="/ui/logs?run_id={run.id}">Лог запуска</a></p>'
        "<script>setTimeout(() => window.location.reload(), 5000);</script>"
        if in_progress
        else f'<p class="muted"><a href="/ui/logs?run_id={run.id}">Лог запуска</a></p>'
    )
    stop = _stop_form(run, "management") if in_progress else ""
    return f"""<section class="band">
  <h2>Запуск #{run.id}: {_MODE_TITLES[run.parameters.mode]} {_badge(overall, overall_badge)}</h2>
  {_progress(db, run)}
  {stop}
  <p>Начат: {escape(run.created_at.astimezone().strftime("%d.%m.%Y %H:%M:%S"))}; выбранных источников: {len(selected)}.</p>
  <table><thead><tr><th>Источник / этап</th><th>Статус</th>{columns}
  <th>Ошибок</th><th>Сообщение</th></tr></thead>
  <tbody>{"".join(rows)}</tbody></table>
  {refresh}
</section>"""


def _management_page(
    db: Session,
    *,
    selected: set[str],
    run: OperationRun | None = None,
    history: Sequence[OperationRun] = (),
    warning: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    definitions = news_sources()
    all_checked = bool(definitions) and all(item.name in selected for item in definitions)
    checked_count = sum(item.name in selected for item in definitions)
    run_html = _run_results(db, run, run.parameters.sources or []) if run else ""
    error_html = f'<p class="warning">{escape(warning)}</p>' if warning else ""
    body = f"""{error_html}
{run_html}
<form method="post" action="/ui/management/run" class="source-form">
  <div class="source-tools">
    <input id="source-search" type="search" placeholder="Поиск по названию или id" autocomplete="off">
    <div class="chips">{_filter_chips(definitions)}</div>
  </div>
  <table id="source-table" class="source-table">
    <thead><tr>
      <th class="pick"><input id="toggle-all-sources" type="checkbox" {"checked" if all_checked else ""} title="Выбрать все видимые"></th>
      <th>Источник</th><th>ID</th><th>Тип</th><th>Последняя загрузка</th><th>Статей в БД</th>
    </tr></thead>
    <tbody>{_source_rows(db, definitions, selected)}</tbody>
  </table>
  <div class="run-bar">
    <button id="run-button" class="run-button" type="submit" {"" if checked_count else "disabled"}>Подгрузить статьи (<span class="selected-count">{checked_count}</span>)</button>
    <button id="resolve-button" class="run-button secondary" type="submit" formaction="/ui/management/resolve" {"" if checked_count else "disabled"}>Разрешить персоны (<span class="selected-count">{checked_count}</span>)</button>
    <span class="muted">Выбрано <span id="selected-total">{checked_count}</span> из {len(definitions)}. Галочки действуют только на этот запуск и не меняют расписание.
    «Подгрузить статьи» скачивает публикации и извлекает людей и события; «Разрешить персоны» привязывает упоминания к людям,
    затем классифицирует и сверяет с РФМ — только после неё новые люди попадают в «Кандидаты».
    Жёлтая дата — не загружался больше {STALE_AFTER.days} дней.</span>
  </div>
</form>
{_history(history, run)}
<script>
const rows = [...document.querySelectorAll('#source-table tbody tr')];
const boxes = rows.map(row => row.querySelector('input[name="sources"]'));
const toggleAll = document.getElementById('toggle-all-sources');
const search = document.getElementById('source-search');
const chips = [...document.querySelectorAll('.chips .chip')];
let kind = 'all';
function refresh() {{
  const shown = rows.filter(row => !row.hidden).map(row => row.querySelector('input[name="sources"]'));
  const checked = boxes.filter(box => box.checked).length;
  document.querySelectorAll('.selected-count').forEach(count => {{ count.textContent = checked; }});
  document.getElementById('selected-total').textContent = checked;
  document.querySelectorAll('.run-button').forEach(button => {{ button.disabled = checked === 0; }});
  toggleAll.checked = shown.length > 0 && shown.every(box => box.checked);
  toggleAll.indeterminate = shown.some(box => box.checked) && !toggleAll.checked;
}}
function applyFilter() {{
  const query = search.value.trim().toLowerCase();
  rows.forEach(row => {{
    row.hidden = (kind !== 'all' && row.dataset.kind !== kind)
      || (query !== '' && !row.dataset.search.includes(query));
  }});
  chips.forEach(chip => chip.classList.toggle('active', chip.dataset.kind === kind));
  refresh();
}}
toggleAll.addEventListener('change', () => {{
  rows.filter(row => !row.hidden).forEach(row => {{
    row.querySelector('input[name="sources"]').checked = toggleAll.checked;
  }});
  refresh();
}});
boxes.forEach(box => box.addEventListener('change', refresh));
search.addEventListener('input', applyFilter);
chips.forEach(chip => chip.addEventListener('click', () => {{ kind = chip.dataset.kind; applyFilter(); }}));
refresh();
</script>"""
    page = _page(
        "Управление",
        body,
        active="management",
        instruction=(
            "Выберите источники и запустите по очереди: «Подгрузить статьи» (загрузка и "
            "извлечение), затем «Разрешить персоны» (привязка людей, классификация, РФМ)."
        ),
        next_action="После запуска здесь появятся общий статус и результат по каждому источнику.",
        db=db,
    )
    page.status_code = status_code
    return page


def _recent_runs(registry: OperationRegistry) -> list[OperationRun]:
    """The latest manual runs with a source selection, newest first."""
    return [
        item
        for item in registry.runs_of(_OPERATION, limit=50)
        if item.parameters.sources is not None
    ][:HISTORY_SIZE]


@router.get("/ui/management", response_class=HTMLResponse)
def ui_management(
    run_id: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),  # noqa: B008
    registry: OperationRegistry = Depends(get_operation_registry),  # noqa: B008
) -> HTMLResponse:
    definitions = news_sources()
    selected = {item.name for item in definitions}
    run: OperationRun | None = None
    if run_id is not None:
        try:
            run = registry.get(run_id)
        except OperationNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Запуск не найден") from exc
        if run.operation.name != _OPERATION or run.parameters.sources is None:
            raise HTTPException(status_code=404, detail="Запуск не найден")
        selected = set(run.parameters.sources)
    history = _recent_runs(registry)
    if run_id is None:
        run = history[0] if history else None
    return _management_page(db, selected=selected, run=run, history=history)


@router.post("/ui/management/run", response_model=None)
async def start_management_run(
    request: Request,
    db: Session = Depends(get_db),  # noqa: B008
    registry: OperationRegistry = Depends(get_operation_registry),  # noqa: B008
) -> HTMLResponse | RedirectResponse:
    """Load and extract the selected sources; resolution is its own button."""
    return await _start(request, db, registry, "load")


@router.post("/ui/management/resolve", response_model=None)
async def start_management_resolution(
    request: Request,
    db: Session = Depends(get_db),  # noqa: B008
    registry: OperationRegistry = Depends(get_operation_registry),  # noqa: B008
) -> HTMLResponse | RedirectResponse:
    """Resolve the persons of the selected sources, then classify and match once."""
    return await _start(request, db, registry, "resolve")


async def _start(
    request: Request, db: Session, registry: OperationRegistry, mode: Literal["load", "resolve"]
) -> HTMLResponse | RedirectResponse:
    form = parse_qs((await request.body()).decode("utf-8", errors="replace"))
    sources = form.get("sources", [])
    selected = list(dict.fromkeys(sources))
    allowed = {item.name for item in news_sources()}
    if not selected:
        return _management_page(
            db,
            selected=set(),
            warning="Выберите хотя бы один новостной источник.",
            history=_recent_runs(registry),
            status_code=400,
        )
    if not set(selected) <= allowed:
        return _management_page(
            db,
            selected=set(selected) & allowed,
            warning="В запросе есть неизвестный или не новостной источник.",
            history=_recent_runs(registry),
            status_code=400,
        )
    try:
        run = registry.start(_OPERATION, OperationParameters(sources=selected, mode=mode))
    except OperationConflictError:
        return _management_page(
            db,
            selected=set(selected),
            warning=(
                "Другой запуск (загрузка или разрешение персон) уже выполняется. "
                "Дождитесь его завершения или остановите его."
            ),
            history=_recent_runs(registry),
            status_code=409,
        )
    return RedirectResponse(f"/ui/management?run_id={run.id}", status_code=303)


@router.post("/ui/management/runs/{run_id}/stop", response_model=None)
async def stop_management_run(
    run_id: int,
    request: Request,
    registry: OperationRegistry = Depends(get_operation_registry),  # noqa: B008
) -> RedirectResponse:
    form = parse_qs((await request.body()).decode("utf-8", errors="replace"))
    back = "/ui/logs" if form.get("back") == ["logs"] else "/ui/management"
    try:
        registry.stop(run_id)
    except OperationNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Запуск не найден") from exc
    # Stopping an already finished run changes nothing: the page shows how it ended.
    return RedirectResponse(f"{back}?run_id={run_id}", status_code=303)
