"""The output of operator runs, live while a run goes on."""

from __future__ import annotations

from collections.abc import Sequence
from html import escape

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from operator_console import (
    OperationNotFoundError,
    OperationRegistry,
    OperationRun,
    OperationRunStatus,
)
from web.dependencies import get_db, get_operation_registry
from web.ui.layout import _page
from web.ui.management import (
    _RUN_STATUS_BADGES,
    _RUN_STATUS_LABELS,
    _badge,
    _local_time,
    _progress,
    _stop_form,
)

router = APIRouter()

RUNS_LISTED = 20
REFRESH_SECONDS = 5
_LIVE = (OperationRunStatus.PENDING, OperationRunStatus.RUNNING)


def _run_list(runs: Sequence[OperationRun], current: OperationRun) -> str:
    rows = "".join(
        f'<tr class="{"current" if run.id == current.id else ""}">'
        f'<td><a href="/ui/logs?run_id={run.id}">#{run.id}</a></td>'
        f"<td>{escape(run.operation.title)}</td>"
        f"<td>{escape(_local_time(run.created_at))}</td>"
        f"<td>{_badge(_RUN_STATUS_LABELS[run.status], _RUN_STATUS_BADGES[run.status])}</td>"
        "</tr>"
        for run in runs
    )
    return f"""<section class="band">
  <h2>Запуски</h2>
  <table><thead><tr><th>Запуск</th><th>Операция</th><th>Начат</th><th>Статус</th></tr></thead>
  <tbody>{rows}</tbody></table>
</section>"""


def _output(title: str, text: str, element_id: str) -> str:
    body = escape(text) if text else '<span class="muted">пусто</span>'
    return f'<h3>{title}</h3><pre id="{element_id}" class="log">{body}</pre>'


def _run_log(db: Session, run: OperationRun) -> str:
    live = run.status in _LIVE
    stop = _stop_form(run, "logs") if live else ""
    progress = (
        _progress(db, run)
        if run.operation.name == "monitor" and run.parameters.sources is not None
        else ""
    )
    error = f'<p class="warning">{escape(run.error)}</p>' if run.error else ""
    refresh = (
        f'<p class="muted">Идёт выполнение: лог обновляется каждые {REFRESH_SECONDS} с '
        "(процесс присылает вывод примерно раз в 15 с).</p>"
        f"<script>setTimeout(() => window.location.reload(), {REFRESH_SECONDS * 1000});</script>"
        if live
        else ""
    )
    return f"""<section class="band">
  <h2>Запуск #{run.id}: {escape(run.operation.title)} {_badge(_RUN_STATUS_LABELS[run.status], _RUN_STATUS_BADGES[run.status])}</h2>
  {progress}
  {stop}
  <p class="muted">Начат: {escape(_local_time(run.created_at))}; код выхода: {run.return_code if run.return_code is not None else "—"}</p>
  <p><code>{escape(" ".join(run.command))}</code></p>
  {error}
  {refresh}
  {_output("Журнал (stderr)", run.stderr, "log-stderr")}
  {_output("Результат (stdout)", run.stdout, "log-stdout")}
  <p class="muted">Хранятся последние символы вывода, начало длинного лога обрезается.</p>
</section>
<script>
document.querySelectorAll('pre.log').forEach(pre => {{ pre.scrollTop = pre.scrollHeight; }});
</script>"""


@router.get("/ui/logs", response_class=HTMLResponse)
def ui_logs(
    run_id: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),  # noqa: B008
    registry: OperationRegistry = Depends(get_operation_registry),  # noqa: B008
) -> HTMLResponse:
    runs = registry.list_runs(limit=RUNS_LISTED)
    run: OperationRun | None = runs[0] if runs else None
    if run_id is not None:
        try:
            run = registry.get(run_id)
        except OperationNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Запуск не найден") from exc
    body = (
        f"{_run_log(db, run)}{_run_list(runs, run)}"
        if run is not None
        else '<p class="muted">Запусков ещё не было.</p>'
    )
    return _page(
        "Логи",
        body,
        active="logs",
        instruction="Вывод запусков загрузки: журнал и результат, пока запуск идёт и после него.",
        next_action="Остановить идущий запуск можно кнопкой «Остановить».",
        db=db,
    )
