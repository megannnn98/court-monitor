"""`POST /api/v1/investigation`: one question — about a named person, or about whom a
publication does not name — answered with the evidence behind every fact and what is
still missing. `GET /api/v1/investigation/{run_id}`: a stored run's result, or how far
it got.

An investigation may ask a model and sources outside, so it is an action: refused from
other origins (`web.csrf`). It writes its own run record and nothing else.
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from application import build_investigation_service
from investigation.models import InvestigationResult, ScenarioName
from investigation.service import RunNotFoundError, UnknownSourceError
from web.dependencies import get_db, session_factory_for

router = APIRouter()

MAX_QUERY = 500
MAX_SOURCES = 20


class InvestigationRequest(BaseModel):
    query: str = Field(min_length=1, max_length=MAX_QUERY)
    # Lowers the configured ceiling for this run; never raises it.
    max_followups: int | None = Field(default=None, ge=0)
    local_only: bool = False
    # The sources to keep to: `local`, `telegram`, or a retriever's name. An unknown one
    # is refused, never ignored.
    allowed_sources: list[str] = Field(default_factory=list, max_length=MAX_SOURCES)
    scenario: ScenarioName | None = None


class InvestigationRun(BaseModel):
    run_id: str
    status: str
    scenario: str | None
    round: int
    usage: dict[str, Any]
    failure: str | None
    result: InvestigationResult | None


@router.post("/investigation", response_model=InvestigationResult)
async def post_investigation(
    body: InvestigationRequest,
    db: Session = Depends(get_db),  # noqa: B008
) -> InvestigationResult:
    service = build_investigation_service(session_factory_for(db))
    try:
        return await service.investigate(
            body.query,
            max_followups=body.max_followups,
            local_only=body.local_only,
            sources=body.allowed_sources,
            scenario=body.scenario,
        )
    except UnknownSourceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/investigation/{run_id}", response_model=InvestigationRun)
def get_investigation(
    run_id: str,
    db: Session = Depends(get_db),  # noqa: B008
) -> InvestigationRun:
    service = build_investigation_service(session_factory_for(db))
    try:
        stored = service.get(run_id)
    except RunNotFoundError:
        stored = None
    if stored is None:
        raise HTTPException(status_code=404, detail="investigation run not found")
    return InvestigationRun(
        run_id=stored.run_id,
        status=stored.status,
        scenario=stored.scenario,
        round=stored.round,
        usage=stored.usage,
        failure=stored.failure,
        result=None if stored.result is None else InvestigationResult.model_validate(stored.result),
    )
