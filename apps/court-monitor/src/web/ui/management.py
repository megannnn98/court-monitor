"""Manual pipeline runs: step 1 loads every news source, steps 2–5 the whole database."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime
from html import escape
from typing import Literal
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from ingestion.sources.source_registry import news_sources
from orchestrator import read as orchestrator_read
from orchestrator.models import MonitoringRunStatus
from orchestrator.operations import (
    OperationNotFoundError,
    OperationRegistry,
    OperationRun,
    OperationRunStatus,
)
from orchestrator.source_health import layout_suspects
from web.dependencies import get_db, get_operation_registry
from web.ui.funnel import funnel_html
from web.ui.layout import _page
from web.ui.pipeline import stepper
from web.ui.run_cards import WHOLE_DATABASE_CARDS, badge, card
from web.ui.spend import run_spent
from web.views import spend
from web.views.funnel import funnel
from web.views.management import (
    _ERRORS_COLUMN,
    _LOAD_COLUMNS,
    _OPERATION,
    _OUTCOMES,
    _RESOLVE_COLUMNS,
    _WHOLE_DATABASE,
    HISTORY_SIZE,
    SOURCE_ERRORS,
    SPENDING_SIZE,
    StepRefused,
    _busy_sources,
    _Column,
    _duration,
    _has_derived_step,
    _Mode,
    _monitoring_runs,
    _recent_runs,
    _resolution_progress,
    _skipped_sources,
    _source_outcome,
    _spent_cell,
    shown_models,
    start_step,
)
from web.views.pipeline import PipelineState, current_state
from web.views.run_cards import MODE_TITLES, RUN_STATUS_BADGES, RUN_STATUS_LABELS, local_time

router = APIRouter()


def _stop_form(run: OperationRun, back: str) -> str:
    """A button that stops a live run; `back` is the page to return to."""
    return (
        f'<form method="post" action="/ui/management/runs/{run.id}/stop" class="stop-form" '
        "onsubmit=\"return confirm('Остановить загрузку? Уже загруженное останется в базе.')\">"
        f'<input type="hidden" name="back" value="{escape(back, quote=True)}">'
        '<button type="submit" class="danger">Остановить</button></form>'
    )


def _column_legend(columns: Sequence[_Column]) -> str:
    items = "".join(
        f"<li><b>{escape(header)}</b> — {escape(meaning)}</li>" for header, meaning, _ in columns
    )
    return f'<ul class="column-legend muted">{items}</ul>'


def _history(runs: Sequence[OperationRun], current: OperationRun | None) -> str:
    if not runs:
        return ""
    rows = "".join(
        f'<tr class="{"current" if current is not None and run.id == current.id else ""}">'
        f'<td><a href="/ui/runs?run_id={run.id}">#{run.id}</a></td>'
        f"<td>{MODE_TITLES[run.parameters.mode]}</td>"
        f"<td>{escape(local_time(run.created_at))}</td>"
        f"<td>{badge(RUN_STATUS_LABELS[run.status], RUN_STATUS_BADGES[run.status])}</td>"
        f'<td class="num">{len(run.parameters.sources or [])}</td>'
        f'<td class="num">{_spent_cell(run)}</td>'
        "</tr>"
        for run in runs
    )
    return f"""<section class="band">
  <h2>Последние ручные запуски</h2>
  <table><thead><tr><th>Запуск</th><th>Что</th><th>Начат</th><th>Статус</th><th>Источников</th><th>Потрачено на модель</th></tr></thead>
  <tbody>{rows}</tbody></table>
</section>"""


def _spending(runs: Sequence[OperationRun]) -> str:
    """The history of what the paid steps spent, a run per row, folded: the money beside
    the runs it went on."""
    paid = [run for run in runs if run.parameters.mode in spend.AI_STAGES][:SPENDING_SIZE]
    if not paid:
        return ""
    # «№», not «#»: the link of the list of runs above is the one with «#».
    rows = "".join(
        f'<tr><td><a href="/ui/runs?run_id={run.id}">№ {run.id}</a></td>'
        f"<td>{MODE_TITLES[run.parameters.mode]}</td>"
        f"<td>{escape(local_time(run.created_at))}</td>"
        f"<td>{badge(RUN_STATUS_LABELS[run.status], RUN_STATUS_BADGES[run.status])}</td>"
        f'<td class="num">{_spent_cell(run)}</td></tr>'
        for run in paid
    )
    total = sum(cost for run in paid if (cost := spend.run_cost(run)) is not None)
    return f"""<details class="band spending">
  <summary>Расходы на модель по запускам: {spend.money(total)} за последние {len(paid)}</summary>
  <table><thead><tr><th>Запуск</th><th>Шаг</th><th>Начат</th><th>Статус</th><th>Потрачено</th></tr></thead>
  <tbody>{rows}</tbody>
  <tfoot><tr><th colspan="4" scope="row">Всего</th><th class="num">{spend.money(total)}</th></tr></tfoot></table>
  <p class="muted">«неизвестно» — запуск, который не дошёл до итогов: остановлен или упал;
  «не записан» — шаг тогда ещё не считал свой расход. Вопросы страницы «Спросить» и запуски из командной строки сюда не входят.</p>
</details>"""


def _published_range_label(run: OperationRun) -> str:
    published_from = run.parameters.published_from
    published_to = run.parameters.published_to
    if published_from is None and published_to is None:
        return ""
    if published_from is not None and published_to is not None:
        label = f"{published_from} — {published_to}"
    elif published_from is not None:
        label = f"с {published_from}"
    else:
        label = f"по {published_to}"
    return f'<p class="muted">Период публикаций: <strong>{escape(label)}</strong></p>'


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


def _source_title(source: str, names: dict[str, str]) -> str:
    return (
        f"{escape(names[source])}<br><code>{escape(source)}</code>"
        if source in names
        else f"<code>{escape(source)}</code>"
    )


def _run_results(db: Session, run: OperationRun, selected: Sequence[str]) -> str:
    """A card per run: status, counts per outcome and, folded, the sources that ran.

    Sources the run never reached are only counted: listed one by one they buried the
    source selection under dozens of «Не запускался» rows."""
    whole_database = WHOLE_DATABASE_CARDS.get(run.parameters.mode or "")
    if whole_database is not None:
        return whole_database(run)
    monitoring_runs = _monitoring_runs(db, run)
    per_source = {item.source: item for item in monitoring_runs if item.source is not None}
    derived = next((item for item in monitoring_runs if item.source is None), None)
    skipped = _skipped_sources(run)
    unreached = [source for source in selected if source not in per_source]
    busy = _busy_sources(db, run, unreached) | {
        source for source, reason in skipped.items() if reason == "already_running"
    }

    names = {item.name: item.source_name for item in news_sources()}
    in_progress = run.status in (OperationRunStatus.PENDING, OperationRunStatus.RUNNING)
    resolving = run.parameters.mode == "resolve"
    columns = (*(_RESOLVE_COLUMNS if resolving else _LOAD_COLUMNS), _ERRORS_COLUMN)
    # The number columns and «Сообщение», for a row without numbers.
    blank = len(columns) + 1
    counts = dict.fromkeys(_OUTCOMES, 0)
    rows: list[str] = []
    for source in selected:
        item = per_source.get(source)
        if item is None:
            if source in busy:
                counts["busy"] += 1
                rows.append(
                    f"<tr><td>{_source_title(source, names)}</td>"
                    f"<td>{badge('Занят', 'partial')}</td>"
                    f'<td colspan="{blank}"></td></tr>'
                )
            else:
                counts["waiting" if in_progress else "not_started"] += 1
            continue
        outcome = _source_outcome(item.status, in_progress=in_progress)
        counts[outcome] += 1
        numbers = "".join(f'<td class="num">{value(item)}</td>' for _, _, value in columns)
        message = escape(item.error_message[:240]) if item.error_message else ""
        rows.append(
            f"<tr><td>{_source_title(source, names)}</td><td>{badge(*_OUTCOMES[outcome])}</td>"
            f'{numbers}<td class="error-text">{message}</td></tr>'
        )

    if _has_derived_step(run):
        if derived is not None:
            derived_status = badge(
                *_OUTCOMES[_source_outcome(derived.status, in_progress=in_progress)]
            )
            derived_metrics = (
                f"Классифицировано: {derived.classifications_created}; совпадений РФМ: "
                f"{derived.rf_matches_created}; ошибок: {derived.error_count}"
            )
        else:
            derived_status = badge("Ожидает", "pending") if in_progress else badge("Не запущен")
            derived_metrics = ""
        rows.append(
            f'<tr class="derived"><td>Общая классификация и сверка с РФМ</td>'
            f'<td>{derived_status}</td><td colspan="{blank}">{derived_metrics}</td></tr>'
        )
    headers = "".join(
        f'<th title="{escape(meaning, quote=True)}">{escape(header)}</th>'
        for header, meaning, _ in columns
    )
    status = None
    if run.status is OperationRunStatus.FAILED and any(
        item.status in (MonitoringRunStatus.COMPLETED, MonitoringRunStatus.COMPLETED_WITH_ERRORS)
        for item in monitoring_runs
    ):
        status = ("Завершено частично: есть ошибки", "partial")
    summary = " ".join(
        badge(f"{_OUTCOMES[outcome][0]}: {count}", _OUTCOMES[outcome][1])
        for outcome, count in counts.items()
        if count
    )
    ran = len(selected) - counts["waiting"] - counts["not_started"]
    return card(
        run,
        where=f"источников выбрано: {len(selected)}",
        status=status,
        body=f"""{_published_range_label(run)}
  {_progress(db, run)}
  <p class="run-summary">{summary}</p>
  <details{" open" if in_progress else ""}>
    <summary>Подробно по источникам ({ran})</summary>
    <table><thead><tr><th>Источник / этап</th><th>Статус</th>{headers}
    <th title="Текст ошибки, если источник упал целиком">Сообщение</th></tr></thead>
    <tbody>{"".join(rows)}</tbody></table>
    {_column_legend(columns)}
  </details>""",
    )


def _source_errors(db: Session) -> str:
    rows = "".join(
        f"<tr><td>{escape(error.source or 'общий проход')}</td>"
        f"<td>{escape(error.stage or '—')}</td>"
        f"<td>{escape(error.error_type or '')}: {escape(error.message or '')}</td>"
        f"<td>{error.created_at.astimezone():%d.%m.%Y}</td></tr>"
        for error in orchestrator_read.source_errors(db, limit=SOURCE_ERRORS)
    )
    table = (
        f"""<table><caption class="visually-hidden">Последние ошибки</caption>
  <thead><tr><th scope="col">Источник</th><th scope="col">Этап</th><th scope="col">Ошибка</th>
  <th scope="col">Когда</th></tr></thead><tbody>{rows}</tbody></table>"""
        if rows
        else '<p class="empty">Ошибок нет.</p>'
    )
    return f"""<section class="band" id="source-errors" aria-labelledby="errors-title">
  <h2 id="errors-title">Ошибки источников</h2>
  {table}
</section>"""


def _layout_suspects(db: Session) -> str:
    """One warning per source, separate from ordinary individual load errors."""
    suspects = layout_suspects(db)
    if not suspects:
        return ""
    rows = "".join(
        f"<li><b>{escape(suspect.source)}</b>: {escape(suspect.text)}</li>" for suspect in suspects
    )
    return f"""<section class="band warning" id="layout-suspects" aria-labelledby="layout-title">
  <h2 id="layout-title">Возможное изменение вёрстки источника</h2>
  <p>Проверьте страницу источника и парсер: это сигнал, а не автоматическое отключение.</p>
  <ul>{rows}</ul>
</section>"""


def _management_page(
    db: Session,
    *,
    run: OperationRun | None = None,
    history: Sequence[OperationRun] = (),
    state: PipelineState | None = None,
    warning: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    definitions = news_sources()
    run_html = (
        _run_results(db, run, run.parameters.sources or []) + run_spent(run, history) if run else ""
    )
    error_html = f'<p class="warning">{escape(warning)}</p>' if warning else ""
    # What the page is opened for comes first: start, see the run, look back. The
    # funnel is the whole base, not a run, so it closes the page.
    body = f"""{error_html}
<form method="post" action="/ui/management/run" class="source-form band">
  <h2>Запуск</h2>
  <div class="run-bar">
    {stepper(state or PipelineState(current="load"), len(definitions))}
    <span class="muted">Шаг 1 скачивает все новостные источники ({len(definitions)}); шаги 2–5
    работают со всей базой.</span>
  </div>
  <details class="date-range">
    <summary>Ограничить даты публикаций для шага 1</summary>
    <label>С <input type="date" name="published_from"></label>
    <label>По <input type="date" name="published_to"></label>
    <p class="muted">Пусто — догрузить всё новое с прошлой загрузки. С датой «с» загрузка
    идёт вглубь, мимо уже сохранённого, и у каждого источника останавливается на статьях
    старше неё; «с» — не раньше рабочей даты (шаг 2 удаляет более ранние). Telegram — не
    глубже 30 дней, Коммерсант — только свежая лента.</p>
  </details>
</form>
{run_html}
{_history(history[:HISTORY_SIZE], run)}
{_spending(history)}
{_layout_suspects(db)}
{_source_errors(db)}
{funnel_html(funnel(db))}"""
    page = _page(
        "Журнал запусков",
        body,
        active="management",
        instruction=(
            "Пять шагов по кругу: подгрузить статьи → очистить от мусора → собрать "
            "сущности → определить фигурантов → выделить политические дела и сверить "
            "с РФМ. «Сделать всё» проходит оставшиеся шаги подряд; по одному нажимается "
            "только подсвеченный шаг."
        ),
        db=db,
    )
    page.status_code = status_code
    return page


@router.get("/ui/management", response_class=RedirectResponse)
def legacy_management(request: Request) -> RedirectResponse:
    query = f"?{request.url.query}" if request.url.query else ""
    return RedirectResponse(f"/ui/runs{query}", status_code=303)


@router.get("/ui/runs", response_class=HTMLResponse)
def ui_management(
    run_id: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),  # noqa: B008
    registry: OperationRegistry = Depends(get_operation_registry),  # noqa: B008
) -> HTMLResponse:
    run: OperationRun | None = None
    if run_id is not None:
        try:
            run = registry.get(run_id)
        except OperationNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Запуск не найден") from exc
        if run.operation.name != _OPERATION or (
            run.parameters.sources is None and run.parameters.mode not in _WHOLE_DATABASE
        ):
            raise HTTPException(status_code=404, detail="Запуск не найден")
    history = _recent_runs(registry)
    if run_id is None:
        run = history[0] if history else None
    return _management_page(db, run=run, history=history, state=current_state(registry))


@router.post("/ui/management/run", response_model=None)
async def start_management_run(
    request: Request,
    db: Session = Depends(get_db),  # noqa: B008
    registry: OperationRegistry = Depends(get_operation_registry),  # noqa: B008
) -> HTMLResponse | RedirectResponse:
    """Step 1: load and extract every news source (or, from the API, the ones sent)."""
    return await _start(request, db, registry, "load")


@router.post("/ui/management/purge", response_model=None)
def start_management_purge(
    request: Request,
    db: Session = Depends(get_db),  # noqa: B008
    registry: OperationRegistry = Depends(get_operation_registry),  # noqa: B008
) -> HTMLResponse | RedirectResponse:
    """Step 2: delete the articles without a criminal case; the whole database."""
    return _start_form_step(request, db, registry, "purge")


@router.post("/ui/management/political", response_model=None)
def start_management_political(
    request: Request,
    db: Session = Depends(get_db),  # noqa: B008
    registry: OperationRegistry = Depends(get_operation_registry),  # noqa: B008
) -> HTMLResponse | RedirectResponse:
    """Step 5: tell persecution from crime and check Rosfinmonitoring."""
    return _start_form_step(request, db, registry, "political")


@router.post("/ui/management/figurants", response_model=None)
def start_management_figurants(
    request: Request,
    db: Session = Depends(get_db),  # noqa: B008
    registry: OperationRegistry = Depends(get_operation_registry),  # noqa: B008
) -> HTMLResponse | RedirectResponse:
    """Step 4: tell who a case is opened against among the entities."""
    return _start_form_step(request, db, registry, "figurants")


@router.post("/ui/management/entities", response_model=None)
def start_management_entities(
    request: Request,
    db: Session = Depends(get_db),  # noqa: B008
    registry: OperationRegistry = Depends(get_operation_registry),  # noqa: B008
) -> HTMLResponse | RedirectResponse:
    """Step 3: rebuild the person entities; the whole database."""
    return _start_form_step(request, db, registry, "entities")


def _refused(
    db: Session, registry: OperationRegistry, warning: str, status_code: int
) -> HTMLResponse:
    return _management_page(
        db,
        warning=warning,
        history=_recent_runs(registry),
        state=current_state(registry),
        status_code=status_code,
    )


def _shown(request: Request, db: Session) -> tuple[str | None, str | None]:
    return shown_models(db, request.query_params.get("em"), request.query_params.get("dm"))


def _start_form_step(
    request: Request,
    db: Session,
    registry: OperationRegistry,
    mode: _Mode,
    *,
    sources: list[str] | None = None,
    published_from: date | None = None,
    published_to: date | None = None,
) -> HTMLResponse | RedirectResponse:
    """A step pressed on a legacy page: the run's page, or this page with the refusal."""
    try:
        entity_model, decision_model = _shown(request, db)
        run = start_step(
            registry,
            mode,
            chain=_chained(request),
            after=request.query_params.get("after"),
            entity_model=entity_model,
            decision_model=decision_model,
            sources=sources,
            published_from=published_from,
            published_to=published_to,
        )
    except StepRefused as refused:
        return _refused(db, registry, refused.message, refused.status_code)
    return RedirectResponse(_started_at(request, run.id), status_code=303)


async def _start(
    request: Request, db: Session, registry: OperationRegistry, mode: Literal["load"]
) -> HTMLResponse | RedirectResponse:
    form = parse_qs((await request.body()).decode("utf-8", errors="replace"))
    published_from = _parse_date_field(form, "published_from")
    published_to = _parse_date_field(form, "published_to")
    if published_from == "invalid" or published_to == "invalid":
        return _refused(db, registry, "Дата должна быть в формате YYYY-MM-DD.", 400)
    if (
        isinstance(published_from, date)
        and isinstance(published_to, date)
        and published_from > published_to
    ):
        return _refused(db, registry, "Дата «С» должна быть не позже даты «По».", 400)
    # The page sends no sources: all of them. The API may still name some.
    return _start_form_step(
        request,
        db,
        registry,
        mode,
        sources=form.get("sources", []),
        published_from=published_from if isinstance(published_from, date) else None,
        published_to=published_to if isinstance(published_to, date) else None,
    )


def _chained(request: Request) -> bool:
    """«Сделать всё»: the step is the first of a chain to the end of the cycle."""
    return request.query_params.get("chain") == "1"


def _started_at(request: Request, run_id: int) -> str:
    return (
        "/ui/cycle" if request.query_params.get("back") == "cycle" else f"/ui/runs?run_id={run_id}"
    )


def _parse_date_field(
    form: dict[str, list[str]], field: Literal["published_from", "published_to"]
) -> date | Literal["invalid"] | None:
    raw = (form.get(field) or [""])[0].strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return "invalid"


@router.post("/ui/management/runs/{run_id}/stop", response_model=None)
async def stop_management_run(
    run_id: int,
    request: Request,
    registry: OperationRegistry = Depends(get_operation_registry),  # noqa: B008
) -> RedirectResponse:
    form = parse_qs((await request.body()).decode("utf-8", errors="replace"))
    back = {"logs": "/ui/logs", "entities": "/ui/entities", "cycle": "/ui/cycle"}.get(
        (form.get("back") or [""])[0], "/ui/runs"
    )
    try:
        if not registry.stop(run_id):
            # The step ended a moment before the press, and its chain has started the
            # next: «Остановить» is a word on the chain, not on a run's number.
            registry.stop_chain_after(run_id)
    except OperationNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Запуск не найден") from exc
    # Stopping an already finished run changes nothing: the page shows how it ended.
    return RedirectResponse(f"{back}?run_id={run_id}", status_code=303)
