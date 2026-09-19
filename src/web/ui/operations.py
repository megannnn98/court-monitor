"""Operator console: preview, confirm and follow routine operations."""

import os
from html import escape
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from monitoring.models import MonitoringRunStatus, MonitoringSettings, MonitoringTrigger
from monitoring.repository import SqlAlchemyMonitoringRepository
from operator_console import (
    OPERATION_DEFINITIONS,
    OperationConflictError,
    OperationNotFoundError,
    OperationParameters,
    OperationRegistry,
    OperationRun,
)
from sources.source_registry import SOURCES
from web.dependencies import get_db, get_operation_registry, session_factory_for
from web.ui.layout import _fmt, _page

router = APIRouter()


def _operation_parameters_from_query(
    source: str | None,
    limit: int | None,
    workers: int | None,
) -> OperationParameters:
    return OperationParameters(source=source or None, limit=limit, workers=workers)


def _operation_form(name: str, params: OperationParameters) -> str:
    every_source = (
        f'<option value="" {"selected" if params.source is None else ""}>Все источники</option>'
        if name == "monitor"
        else ""
    )
    source_options = every_source + "".join(
        f'<option value="{escape(source)}" {"selected" if source == params.source else ""}>{escape(source)}</option>'
        for source in sorted(SOURCES)
    )
    source_field = (
        f"""<label>Источник
  <select name="source">{source_options}</select>
</label>"""
        if name in {"monitor", "discover-and-ingest", "extract-entities"}
        else ""
    )
    workers_field = (
        f"""<label>Workers
  <input type="number" name="workers" min="1" max="32" value="{params.workers or 1}">
</label>"""
        if name == "resolve-people"
        else ""
    )
    return f"""<form method="get" class="operation-form">
  {source_field}
  <label>Limit
    <input type="number" name="limit" min="1" max="100000" value="{params.limit or 100}">
  </label>
  {workers_field}
  <button>Preview</button>
</form>"""


def _operation_query(params: OperationParameters) -> str:
    values = {key: value for key, value in params.model_dump().items() if value is not None}
    return urlencode(values)


def _run_badge(status: str) -> str:
    return f'<span class="badge {escape(status)}">{escape(status)}</span>'


def _catch_up_progress(run: OperationRun, db: Session) -> str:
    """Progress of «Докачать»: its `monitor` process leaves one monitoring run per source,
    then one derived run; they are read from monitoring_runs while it goes on."""
    if run.operation.name != "monitor" or run.started_at is None:
        return ""
    runs = SqlAlchemyMonitoringRepository(session_factory_for(db)).list_runs_started_between(
        run.started_at, run.finished_at, trigger=MonitoringTrigger.MANUAL
    )
    # The process sees the same environment as this API process.
    source_total = (
        1 if run.parameters.source else len(MonitoringSettings.from_env(os.environ).enabled_sources)
    )
    source_runs = [item for item in runs if item.source is not None]
    finished_sources = [
        item for item in source_runs if item.status is not MonitoringRunStatus.RUNNING
    ]
    current = next(
        (item.source for item in source_runs if item.status is MonitoringRunStatus.RUNNING), None
    )
    failed = [
        item.source or ""
        for item in source_runs
        if item.status in {MonitoringRunStatus.FAILED, MonitoringRunStatus.ABORTED}
    ]
    derived = next((item for item in runs if item.source is None), None)
    if derived is None:
        derived_state = "не выполнялась" if run.finished_at is not None else "ожидает"
    elif derived.status is MonitoringRunStatus.RUNNING:
        derived_state = "идёт"
    elif derived.status in {MonitoringRunStatus.FAILED, MonitoringRunStatus.ABORTED}:
        derived_state = "ошибка"
    else:
        derived_state = "готово"
    done = len(finished_sources) + int(
        derived is not None and derived.status is not MonitoringRunStatus.RUNNING
    )
    total = source_total + 1
    now = f" · сейчас: {escape(current)}" if current else ""
    failed_names = f" ({escape(', '.join(failed))})" if failed else ""
    return f"""<section class="band catch-up-progress">
  <h2>Прогресс</h2>
  <p><progress value="{done}" max="{total}"></progress> {done} из {total} шагов</p>
  <p>Источники: {len(finished_sources)} из {source_total} готово{now}</p>
  <p>Общая обработка: {derived_state}</p>
  <p>Загружено документов: {sum(item.documents_ingested for item in source_runs)}
    · новых людей: {sum(item.persons_created for item in source_runs)}
    · источников с ошибкой: {len(failed)}{failed_names}</p>
</section>"""


@router.get("/ui/operations")
def ui_operations(
    db: Session = Depends(get_db),  # noqa: B008
    registry: OperationRegistry = Depends(get_operation_registry),  # noqa: B008
) -> HTMLResponse:
    cards = []
    for definition in registry.definitions():
        cards.append(
            f"""<section class="operation-card">
  <h2>{escape(definition.title)}</h2>
  <p>{escape(definition.description)}</p>
  <p class="muted">{escape(definition.next_action)}</p>
  <a class="primary" href="/ui/operations/{definition.name}">Настроить</a>
</section>"""
        )
    runs = "".join(
        f"""<tr>
  <td><a href="/ui/operations/runs/{run.id}">{run.id}</a></td>
  <td>{escape(run.operation.title)}</td>
  <td>{_run_badge(run.status.value)}</td>
  <td>{_fmt(run.started_at)}</td>
  <td>{_fmt(run.duration_seconds)}</td>
</tr>"""
        for run in registry.list_runs()[:20]
    )
    return _page(
        "Операции",
        f"""<section class="operation-grid">{"".join(cards)}</section>
<h2>Последние runs</h2>
<table><thead><tr><th>ID</th><th>Операция</th><th>Status</th><th>Started</th><th>Duration</th></tr></thead><tbody>{runs}</tbody></table>""",
        active="operations",
        instruction="Здесь запускаются routine pipeline operations на живой базе.",
        next_action="Выберите операцию, проверьте preview и подтвердите run.",
        db=db,
        warning="Все операции на этой странице могут менять данные или занимать долгое время.",
    )


@router.get("/ui/operations/{name}")
def ui_operation_preview(
    name: str,
    source: str | None = None,
    limit: int | None = Query(default=None, ge=1, le=100_000),
    workers: int | None = Query(default=None, ge=1, le=32),
    db: Session = Depends(get_db),  # noqa: B008
    registry: OperationRegistry = Depends(get_operation_registry),  # noqa: B008
) -> HTMLResponse:
    try:
        definition = registry.definition(name)
        params = registry.prepare_parameters(
            definition, _operation_parameters_from_query(source, limit, workers)
        )
    except (OperationNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    try:
        command = OPERATION_DEFINITIONS[name].name
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Operation not found") from exc
    confirm_query = _operation_query(params)
    return _page(
        definition.title,
        f"""{_operation_form(name, params)}
<section class="band">
  <h2>Preview</h2>
  <dl>
    <dt>Operation</dt><dd>{escape(command)}</dd>
    <dt>Source</dt><dd>{_fmt(params.source)}</dd>
    <dt>Limit</dt><dd>{_fmt(params.limit)}</dd>
    <dt>Workers</dt><dd>{_fmt(params.workers)}</dd>
  </dl>
  <form method="post" action="/ui/operations/{escape(name)}/confirm?{escape(confirm_query)}">
    <button>Подтвердить run</button>
  </form>
</section>""",
        active="operations",
        instruction=definition.description,
        next_action=definition.next_action,
        db=db,
        warning=definition.warning,
    )


@router.post("/ui/operations/{name}/confirm", response_model=None)
def ui_operation_confirm(
    name: str,
    source: str | None = None,
    limit: int | None = Query(default=None, ge=1, le=100_000),
    workers: int | None = Query(default=None, ge=1, le=32),
    registry: OperationRegistry = Depends(get_operation_registry),  # noqa: B008
) -> RedirectResponse:
    try:
        run = registry.start(name, _operation_parameters_from_query(source, limit, workers))
    except OperationNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Operation not found") from exc
    except (OperationConflictError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return RedirectResponse(f"/ui/operations/runs/{run.id}", status_code=303)


@router.get("/ui/operations/runs/{run_id}")
def ui_operation_run(
    run_id: int,
    db: Session = Depends(get_db),  # noqa: B008
    registry: OperationRegistry = Depends(get_operation_registry),  # noqa: B008
) -> HTMLResponse:
    try:
        run = registry.get(run_id)
    except OperationNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Operation run not found") from exc
    output = escape(run.stdout or run.stderr or run.error or "Пока нет вывода.")
    refresh = (
        '<meta http-equiv="refresh" content="2">'
        if run.status.value in {"pending", "running"}
        else ""
    )
    body = f"""{refresh}
{_catch_up_progress(run, db)}
<section class="band">
  <dl>
    <dt>Status</dt><dd>{_run_badge(run.status.value)}</dd>
    <dt>Operation</dt><dd>{escape(run.operation.title)}</dd>
    <dt>Started</dt><dd>{_fmt(run.started_at)}</dd>
    <dt>Finished</dt><dd>{_fmt(run.finished_at)}</dd>
    <dt>Duration</dt><dd>{_fmt(run.duration_seconds)}</dd>
    <dt>Return code</dt><dd>{_fmt(run.return_code)}</dd>
  </dl>
</section>
<h2>Command</h2>
<pre>{" ".join(escape(part) for part in run.command)}</pre>
<h2>Output</h2>
<pre>{output}</pre>"""
    return _page(
        f"Run #{run.id}",
        body,
        active="operations",
        instruction="Run detail показывает состояние и последние строки вывода операции.",
        next_action="Дождитесь завершения, затем проверьте counts/status или откройте новую операцию.",
        db=db,
        warning="При reload running run не перезапускается: страница только перечитывает состояние.",
    )
