"""Operator operation runs (read-only REST)."""

from fastapi import APIRouter, Depends, HTTPException

from operator_console import (
    OperationNotFoundError,
    OperationRegistry,
    operation_run_to_dict,
)
from web.dependencies import get_operation_registry
from web.response_models import OperationRunResponse

router = APIRouter()


@router.get("/operations/runs", response_model=list[OperationRunResponse])
def list_operation_runs(
    registry: OperationRegistry = Depends(get_operation_registry),  # noqa: B008
) -> list[OperationRunResponse]:
    return [OperationRunResponse(**operation_run_to_dict(run)) for run in registry.list_runs()]


@router.get("/operations/runs/{run_id}", response_model=OperationRunResponse)
def get_operation_run(
    run_id: int,
    registry: OperationRegistry = Depends(get_operation_registry),  # noqa: B008
) -> OperationRunResponse:
    try:
        return OperationRunResponse(**operation_run_to_dict(registry.get(run_id)))
    except OperationNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Operation run not found") from exc
