"""Task-centric operator dashboard over the existing pipeline and review queues."""

from __future__ import annotations

from html import escape

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from operator_console import OperationRegistry
from web.dependencies import get_db, get_operation_registry
from web.ui import spend
from web.ui.layout import _page
from web.ui.pipeline import (
    STAGES,
    PipelineState,
    ask,
    chain_action,
    chain_confirmation,
    chain_note,
    chain_span,
    chain_stopped,
    current_state,
)
from web.ui.run_tail import tail_html
from web.ui.workload import OperatorTask, Workload, next_operator_task, operator_tasks, workload

router = APIRouter()

PROCESSING_LABELS = {
    "load": "Публикации загружены",
    "purge": "Публикации проверены",
    "entities": "Люди определены",
    "figurants": "Фигуранты определены",
    "political": "Проверка политичности завершена",
}


def _attention(task: OperatorTask | None, state: PipelineState) -> str:
    if task is not None:
        return f"""<section class="operator-attention" data-primary-task="{task.key}">
  <p class="section-kicker">Требует вашего внимания</p>
  <h2>{escape(task.title)}: {task.count}</h2>
  <p>{escape(task.description)}</p>
  <a class="primary-action" href="{task.href}">Начать проверку</a>
</section>"""
    if state.live is not None:
        return """<section class="operator-attention processing-attention">
  <p class="section-kicker">Состояние системы</p>
  <h2>Идёт автоматическая обработка</h2>
  <p>Результаты появятся после завершения текущего шага.</p>
</section>"""
    return """<section class="operator-attention">
  <p class="section-kicker">Требует вашего внимания</p>
  <h2>Сейчас ничего проверять не нужно</h2>
  <p>Автоматическая обработка завершена или ожидает следующего запуска.</p>
</section>"""


def _next_tasks(work: Workload, current: OperatorTask | None) -> str:
    tasks = [task for task in operator_tasks(work) if task.count and task != current]
    if not tasks:
        return ""
    rows = "".join(
        f'<a href="{task.href}"><span>{escape(task.title)}</span><strong>{task.count}</strong></a>'
        for task in tasks
    )
    return f"""<section class="next-tasks" aria-labelledby="next-tasks-title">
  <h2 id="next-tasks-title">Далее</h2>
  {rows}
</section>"""


def chain_question(state: PipelineState) -> str:
    """What is asked before «Сделать всё» on «Работа»: only what it costs. Read by the
    legacy page and `GET /api/v1/cycle`."""
    return chain_confirmation(state.current)


def step_statuses(state: PipelineState) -> list[tuple[str, str]]:
    """(stage, status) of each step of the round: done, running, ready or waiting."""
    current = STAGES.index(state.current)
    return [
        (
            stage,
            "done"
            if index < current
            else ("running" if state.live is not None else "ready")
            if index == current
            else "waiting",
        )
        for index, stage in enumerate(STAGES)
    ]


def _pipeline_control(state: PipelineState, work: Workload) -> str:
    if state.live is not None:
        return (
            f'<button class="danger" type="submit" formaction="/ui/management/runs/{state.live.id}/stop" '
            'name="back" value="cycle" '
            "onclick=\"return confirm('Остановить запуск? Уже сделанное останется.')\">"
            "Остановить</button>"
        )
    confirmation = chain_question(state)
    # One press runs every step that is left; a single step is on «Журнал запусков».
    return (
        f'<button id="step-{state.current}" class="secondary" type="submit" '
        f'formaction="{chain_action(state)}&amp;back=cycle"{ask(confirmation)}>'
        "Сделать всё</button> "
        f'<span class="muted">{chain_span(state.current)}; по одному шагу — в '
        '<a href="/ui/runs">журнале запусков</a></span>'
    )


def _processing(state: PipelineState, work: Workload) -> str:
    marks = {
        "done": ("✓", "готово"),
        "running": ("●", "выполняется"),
        "ready": ("○", "ожидает запуска"),
        "waiting": ("○", "ожидает"),
    }
    rows = []
    for stage, css in step_statuses(state):
        marker, status = marks[css]
        rows.append(
            f'<li class="{css}"><span aria-hidden="true">{marker}</span>'
            f"{escape(PROCESSING_LABELS[stage])}<small>{status}</small></li>"
        )
    return f"""<section class="processing-status" aria-labelledby="processing-title">
  <div><h2 id="processing-title">Обработка данных</h2>
  <p class="muted">Автоматические шаги. Ручная проверка показана выше.</p></div>
  {spend.balance_line()}{spend.notice(state.current, last=state.last_spent) if state.live is None else ""}
  <ol>{"".join(rows)}</ol>
  {tail_html(state.live) if state.live is not None else ""}
  {chain_note(state)}{chain_stopped(state)}
  <div class="pipeline-current {"running" if state.live is not None else "ready"}">{_pipeline_control(state, work)}</div>
</section>"""


@router.get("/ui/cycle", response_class=HTMLResponse)
def ui_cycle(
    db: Session = Depends(get_db),  # noqa: B008
    registry: OperationRegistry = Depends(get_operation_registry),  # noqa: B008
) -> HTMLResponse:
    work = workload(db)
    state = current_state(registry)
    refresh = (
        "<script>setTimeout(() => window.location.reload(), 5000);</script>"
        if state.live is not None
        else ""
    )
    task = next_operator_task(work)
    body = f'<form method="post" class="operator-dashboard">{_attention(task, state)}{_next_tasks(work, task)}{_processing(state, work)}</form>{refresh}'
    return _page(
        "Работа",
        body,
        active="cycle",
        instruction="Сначала выполните одну показанную проверку; состояние автоматической обработки ниже.",
        db=db,
        work=work,
    )
