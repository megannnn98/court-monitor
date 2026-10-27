"""«Работа» for the React console: the reviews that wait, the automatic steps, and the
two actions of the legacy page — «Сделать всё» and «Остановить». Both are refused from
other origins (`web.csrf`, ADR 0022); starting a single step stays on the legacy
«Журнал запусков»."""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from llm.chat import Purpose
from orchestrator.operations import OperationNotFoundError, OperationRegistry, OperationRunStatus
from web.dependencies import get_db, get_operation_registry
from web.response_models import (
    CycleResponse,
    CycleStartRequest,
    CycleStartResponse,
    CycleStepResponse,
    CycleTaskResponse,
    EntityModelNoticeResponse,
    LastLoadResponse,
    LiveRunResponse,
    StopResponse,
)
from web.views import spend
from web.views.cycle import PROCESSING_LABELS, chain_question, step_statuses
from web.views.management import StepRefused, shown_models, start_step, working_date
from web.views.pipeline import (
    OPERATION,
    STAGES,
    chain_confirmation,
    chain_note_text,
    chain_span,
    chain_stopped_text,
    current_state,
    latest_run_id,
    title_of,
)
from web.views.workload import OperatorTask, next_operator_task, operator_tasks, workload

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
        chain_question=chain_question(state, with_model=False),
        chain_note=chain_note_text(state),
        chain_stopped=chain_stopped_text(state),
        entity_model=_entity_model(state.current) if state.live is None else None,
        decision_model=(_entity_model(state.current, "decision") if state.live is None else None),
        working_date=working_date(),
        period_question=chain_confirmation("load", with_model=False),
        last_load=_last_load(registry),
    )


# How far back «Работа» looks for the latest load that ended well.
_LOAD_HISTORY = 50


def _day(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def _last_load(registry: OperationRegistry) -> LastLoadResponse | None:
    for run in registry.runs_of(OPERATION, limit=_LOAD_HISTORY):
        if run.parameters.mode == "load" and run.status is OperationRunStatus.SUCCEEDED:
            parameters = run.parameters
            return LastLoadResponse(
                finished_at=run.finished_at,
                published_from=_day(parameters.published_from),
                published_to=_day(parameters.published_to),
            )
    return None


def _entity_model(stage: str, kind: Purpose = "extraction") -> EntityModelNoticeResponse | None:
    """The model of `kind` the round from `stage` would ask; None when no step left asks it."""
    if not any(kind in spend.STAGE_KINDS.get(step, ()) for step in STAGES[STAGES.index(stage) :]):
        return None
    notice = spend.entity_model_notice(kind=kind)
    if notice is None:
        return None
    price = notice.info.price
    return EntityModelNoticeResponse(
        model=notice.model,
        label=notice.label,
        free=notice.info.free,
        prompt_usd_per_million=price.prompt if price else None,
        completion_usd_per_million=price.completion if price else None,
        price_stale=notice.info.price_stale,
        kind=notice.kind,
        price_text=notice.price,
    )


@router.post("/cycle/start", response_model=CycleStartResponse)
def start_cycle(
    body: CycleStartRequest,
    db: Session = Depends(get_db),  # noqa: B008
    registry: OperationRegistry = Depends(get_operation_registry),  # noqa: B008
) -> CycleStartResponse:
    """«Сделать всё»: the current step and, after it, every step to the end of the round.
    409 when a run is live, when `after` is not the latest run any more (an old press),
    or when another worker started first."""
    state = current_state(registry)
    try:
        shown_models(db, body.entity_model, body.decision_model)
        # With dates the round is steps 1–5 for them; without, from where it is.
        period = body.published_from is not None or body.published_to is not None
        stage = "load" if period else state.current
        run = start_step(
            registry,
            stage,
            chain=True,
            after=str(body.after),
            published_from=body.published_from,
            published_to=body.published_to,
            entity_model=body.entity_model,
            decision_model=body.decision_model,
        )
    except StepRefused as refused:
        raise HTTPException(status_code=refused.status_code, detail=refused.message) from None
    return CycleStartResponse(run_id=run.id, stage=stage)


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
