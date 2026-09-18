"""The main product query: politically persecuted persons absent from Rosfinmonitoring."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from candidates.service import CandidateQueryService
from web.dependencies import get_db
from web.response_models import CandidateResponse

router = APIRouter()


# Candidate endpoints
@router.get("/candidates", response_model=list[CandidateResponse])
def list_candidates(
    snapshot_id: int = Query(..., description="Rosfinmonitoring snapshot ID"),
    min_persecution_confidence: float = Query(default=0.7, ge=0.0, le=1.0),
    limit: int = Query(default=100, ge=1, le=1000),
    db: Session = Depends(get_db),  # noqa: B008
) -> list[CandidateResponse]:
    """List politically persecuted persons absent from Rosfinmonitoring."""
    service = CandidateQueryService(db)
    try:
        result = service.get_candidates(
            snapshot_id=snapshot_id,
            min_persecution_confidence=min_persecution_confidence,
            limit=limit,
            session=db,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return [
        CandidateResponse(
            person_id=c.person_id,
            canonical_name=c.canonical_name,
            normalized_name=c.normalized_name,
            persecution_status=c.persecution_status,
            persecution_confidence=c.persecution_confidence,
            persecution_reasons=c.persecution_reasons,
            rosfinmonitoring_status=c.rosfinmonitoring_status,
            rosfinmonitoring_match_confidence=c.rosfinmonitoring_match_confidence,
            event_count=c.event_count,
            alias_count=c.alias_count,
        )
        for c in result.candidates
    ]
