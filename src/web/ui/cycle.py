"""The operator's work cycle: existing steps and review stations in one order."""

from __future__ import annotations

from html import escape

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from db.orm_models import EntityGroupPoliticsRecord
from operator_console import OperationRegistry, OperationRun, OperationRunStatus
from web.dependencies import get_db, get_operation_registry
from web.ui.layout import _page
from web.ui.management import recent_source_errors
from web.ui.pipeline import (
    HINTS,
    STAGES,
    PipelineState,
    current_state,
    step_action,
    step_confirmation,
)
from web.ui.workload import Workload, workload

router = APIRouter()

_LABELS = {
    "load": "Подгрузить",
    "purge": "Очистить",
    "entities": "Сущности",
    "figurants": "Фигуранты",
    "political": "Политические дела",
}
_STATUS = {
    OperationRunStatus.PENDING: "ждёт запуска",
    OperationRunStatus.RUNNING: "выполняется",
    OperationRunStatus.SUCCEEDED: "завершён",
    OperationRunStatus.FAILED: "ошибка",
    OperationRunStatus.INTERRUPTED: "остановлен",
}


def _latest_by_stage(runs: list[OperationRun]) -> dict[str, OperationRun]:
    result: dict[str, OperationRun] = {}
    for run in runs:
        stage = run.parameters.mode
        if stage in STAGES and stage not in result:
            result[stage] = run
    return result


def _last_result(run: OperationRun | None) -> str:
    if run is None:
        return "Ещё не запускался."
    moment = run.finished_at or run.started_at or run.created_at
    return (
        f"Последний запуск #{run.id}: {_STATUS[run.status]}, {moment.astimezone():%d.%m.%Y %H:%M}."
    )


def _pending_review(stage: str, work: Workload) -> tuple[str, int] | None:
    pending = {
        "entities": ("Отсев", work.junk_holds),
        "figurants": ("Пары", work.pairs),
        "political": ("Роль", work.unclear_roles),
    }.get(stage)
    return pending if pending is not None and pending[1] else None


def _step(state: PipelineState, stage: str, run: OperationRun | None, work: Workload) -> str:
    index = STAGES.index(stage)
    current = STAGES.index(state.current)
    if stage == state.current and state.live is not None:
        status, status_label = "running", "выполняется"
        control = (
            f'<button class="danger" type="submit" formaction="/ui/management/runs/{state.live.id}/stop" '
            'name="back" value="cycle" '
            "onclick=\"return confirm('Остановить запуск? Уже сделанное останется.')\">"
            "Остановить</button>"
        )
    elif stage == state.current:
        status, status_label = "ready", "можно запускать"
        confirmations = [step_confirmation(stage)]
        pending = _pending_review(stage, work)
        if pending is not None:
            title, count = pending
            confirmations.insert(
                0,
                f"Предыдущая проверка «{title}» не завершена: {count}. Всё равно запустить шаг?",
            )
        confirmation = " ".join(item for item in confirmations if item)
        onclick = (
            f" onclick=\"return confirm('{escape(confirmation, quote=True)}')\""
            if confirmation
            else ""
        )
        control = (
            f'<button id="step-{stage}" type="submit" '
            f'formaction="{step_action(stage)}?back=cycle"{onclick}>Запустить</button>'
        )
    else:
        done = index < current
        status, status_label = ("done", "готово") if done else ("waiting", "ожидает")
        control = '<button type="button" disabled>Готово</button>' if done else ""
    return f"""<section class="cycle-station step-station {status}">
  <div class="cycle-number">{index + 1}</div>
  <div><h2>{index + 1}. {escape(_LABELS[stage])}</h2>
  <p class="station-state">{status_label}</p>
  <p>{escape(HINTS[stage])}.</p>
  <p class="muted">{escape(_last_result(run))}</p></div>
  <div class="station-action">{control}</div>
</section>"""


def _review(
    title: str,
    href: str,
    count: int,
    note: str,
    *,
    informational: bool = False,
    action: str | None = None,
) -> str:
    kind = "info" if informational else ("warning" if count else "review")
    if action is not None:
        action = f"{action} ({count})" if count else action
    elif informational or not count:
        action = "Открыть"
    else:
        action = f"Разобрать ({count})"
    return f"""<section class="cycle-station review-station {kind}">
  <div class="cycle-rail" aria-hidden="true">↓</div>
  <div><h2>{escape(title)} <span class="count">{count}</span></h2>
  <p>{escape(note)}</p></div>
  <div class="station-action"><a class="button-link" href="{href}">{action}</a></div>
</section>"""


def _stations(
    state: PipelineState,
    latest: dict[str, OperationRun],
    work: Workload,
    failures: int,
    result_count: int,
) -> str:
    return "".join(
        (
            _step(state, "load", latest.get("load"), work),
            _review(
                "Сбои извлечения",
                "/ui/runs#source-errors",
                failures,
                "Справочно: ошибки отдельных источников не блокируют цикл.",
                informational=True,
            ),
            _step(state, "purge", latest.get("purge"), work),
            _review(
                "Отсев",
                "/ui/junk-holds",
                work.junk_holds,
                "Проверить удержанные публикации до следующей очистки.",
            ),
            _step(state, "entities", latest.get("entities"), work),
            _review("Пары", "/ui/pairs", work.pairs, "Решить спорные совпадения людей."),
            _step(state, "figurants", latest.get("figurants"), work),
            _review(
                "Роль",
                "/ui/roles",
                work.unclear_roles,
                "Проверить неясные роли в деле.",
                action="Проверить",
            ),
            _step(state, "political", latest.get("political"), work),
            _review(
                "Политичность",
                "/ui/politics-review",
                work.unclear_verdicts,
                "Проверить дела с неясной политичностью.",
                action="Проверить",
            ),
            _review(
                "Безымянные",
                "/ui/unnamed",
                work.unnamed,
                "Установить фигурантов, которых публикация не называет.",
            ),
            _review(
                "Результат",
                "/ui/political",
                result_count,
                "Посмотреть итоговый список после завершения цикла.",
                informational=True,
            ),
        )
    )


@router.get("/ui/cycle", response_class=HTMLResponse)
def ui_cycle(
    db: Session = Depends(get_db),  # noqa: B008
    registry: OperationRegistry = Depends(get_operation_registry),  # noqa: B008
) -> HTMLResponse:
    runs = registry.runs_of("monitor", limit=50)
    work = workload(db)
    result_count = (
        db.scalar(
            select(func.count())
            .select_from(EntityGroupPoliticsRecord)
            .where(EntityGroupPoliticsRecord.verdict == "political")
        )
        or 0
    )
    body = f'<form method="post" class="cycle">{_stations(current_state(registry), _latest_by_stage(runs), work, recent_source_errors(db), result_count)}</form>'
    return _page(
        "Рабочий цикл",
        body,
        active="cycle",
        instruction="Шаги обработки и места проверки идут сверху вниз; запускается только текущий шаг.",
        db=db,
        work=work,
    )
