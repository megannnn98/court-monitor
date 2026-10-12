"""`POST /api/v1/investigation`: one question about a person, answered from what is
stored, with the evidence behind every fact and what is still missing.

An investigation may ask a model, so it is an action: refused from other origins
(`web.csrf`). It writes nothing to the database.
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from application import build_investigation_service
from investigation.models import InvestigationResult
from web.dependencies import get_db, session_factory_for

router = APIRouter()

MAX_QUERY = 500


class InvestigationRequest(BaseModel):
    query: str = Field(min_length=1, max_length=MAX_QUERY)
    # Lowers the configured ceiling for this run; never raises it.
    max_followups: int | None = Field(default=None, ge=0)
    local_only: bool = False


@router.post("/investigation", response_model=InvestigationResult)
async def post_investigation(
    body: InvestigationRequest,
    db: Session = Depends(get_db),  # noqa: B008
) -> InvestigationResult:
    service = build_investigation_service(session_factory_for(db))
    return await service.investigate(
        body.query, max_followups=body.max_followups, local_only=body.local_only
    )
