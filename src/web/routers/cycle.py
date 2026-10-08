"""«Работа» for the React console: the reviews that wait, the automatic steps, and the
two actions of the legacy page — «Сделать всё» and «Остановить». Both are refused from
other origins (`web.csrf`, ADR 0022); starting a single step stays on the legacy
«Журнал запусков»."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from operator_console import OperationNotFoundError, OperationRegistry
from web.dependencies import get_db, get_operation_registry
from web.response_models import (
    CycleResponse,
    CycleStartRequest,
    CycleStartResponse,
    CycleStepResponse,
    CycleTaskResponse,
    LiveRunResponse,
    StopResponse,
)
from web.ui.cycle import PROCESSING_LABELS, chain_question, step_statuses
from web.ui.management import StepRefused, start_step
from web.ui.pipeline import (
    STAGES,
    chain_note_text,
    chain_span,
    chain_stopped_text,
    current_state,
    latest_run_id,
    title_of,
)
from web.ui.workload import OperatorTask, next_operator_task, operator_tasks, workload

router = APIRouter()


def _task(task: OperatorTask) -> CycleTaskResponse:
    return CycleTaskResponse(
        key=task.key,
        title=task.title,
        count=task.count,
        href=task.href,
        description=task.description,
    )


@router.get("/cycle", response_model=CycleResponse)
def get_cycle(
    db: Session = Depends(get_db),  # noqa: B008
    registry: OperationRegistry = Depends(get_operation_registry),  # noqa: B008
) -> CycleResponse:
    """What «Работа» shows: the first review to do, the other non-empty queues, and the
    state of the five automatic steps."""
    work = workload(db)
    state = current_state(registry)
    first = next_operator_task(work)
    return CycleResponse(
        attention=_task(first) if first is not None else None,
        tasks=[_task(task) for task in operator_tasks(work) if task.count and task != first],
        steps=[
            CycleStepResponse(
                stage=stage,
                number=STAGES.index(stage) + 1,
                label=PROCESSING_LABELS[stage],
                status=status,
            )
            for stage, status in step_statuses(state)
        ],
        current_stage=state.current,
        live=(
            LiveRunResponse(run_id=state.live.id, title=title_of(state.live))
            if state.live is not None
            else None
        ),
        latest_run_id=latest_run_id(state),
        chain_span=chain_span(state.current),
        chain_question=chain_question(state),
        chain_note=chain_note_text(state),
        chain_stopped=chain_stopped_text(state),
    )


@router.post("/cycle/start", response_model=CycleStartResponse)
def start_cycle(
    body: CycleStartRequest,
    registry: OperationRegistry = Depends(get_operation_registry),  # noqa: B008
) -> CycleStartResponse:
    """«Сделать всё»: the current step and, after it, every step to the end of the round.
    409 when a run is live, when `after` is not the latest run any more (an old press),
    or when another worker started first."""
    state = current_state(registry)
    try:
        run = start_step(registry, state.current, chain=True, after=str(body.after))
    except StepRefused as refused:
        raise HTTPException(status_code=refused.status_code, detail=refused.message) from None
    return CycleStartResponse(run_id=run.id, stage=state.current)


@router.post("/cycle/runs/{run_id}/stop", response_model=StopResponse)
def stop_cycle_run(
    run_id: int,
    registry: OperationRegistry = Depends(get_operation_registry),  # noqa: B008
) -> StopResponse:
    """«Остановить»: the run, or the step its chain started after it ended a moment ago.
    What was done stays."""
    try:
        stopped = registry.stop(run_id) or registry.stop_chain_after(run_id)
    except OperationNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Запуск не найден") from exc
    return StopResponse(run_id=run_id, stopped=stopped)
