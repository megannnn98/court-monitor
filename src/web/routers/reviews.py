"""Review records and entity-resolution review decisions."""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from persons.persistence import SqlAlchemyPersonPersistence
from persons.resolution.review import (
    PersonResolutionReviewService,
    ResolutionReviewAction,
    ResolutionReviewNotFoundError,
    ResolutionReviewResult,
    ResolutionReviewStateError,
    ResolutionReviewView,
)
from web.dependencies import get_db, session_factory_for
from web.response_models import ReviewResponse

router = APIRouter()


# Review endpoints
@router.get("/reviews", response_model=list[ReviewResponse])
def list_reviews(
    status: str | None = Query(default=None),
    subject_type: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),  # noqa: B008
) -> list[ReviewResponse]:
    """List reviews."""
    from db.orm_models import ReviewRecordModel

    query = select(ReviewRecordModel).offset(offset).limit(limit)

    if status:
        query = query.where(ReviewRecordModel.decision == status)

    if subject_type:
        query = query.where(ReviewRecordModel.subject_type == subject_type)

    query = query.order_by(ReviewRecordModel.created_at.desc(), ReviewRecordModel.id.desc())

    reviews = db.scalars(query).all()

    return [
        ReviewResponse(
            id=r.id,
            subject_type=r.subject_type,
            subject_id=r.subject_id,
            decision=r.decision,
            confidence=r.confidence,
            reason=r.reason,
            reviewer_note=r.reviewer_note,
            created_at=r.created_at.isoformat(),
            reviewed_at=r.reviewed_at.isoformat() if r.reviewed_at else None,
        )
        for r in reviews
    ]


# Person resolution (ER v2) review endpoints. Not under /persons/{person_id}.
class PersonResolutionReviewDecisionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: ResolutionReviewAction
    person_id: int | None = None
    source_person_id: int | None = None
    note: str | None = Field(default=None, max_length=2000)


def _person_resolution_reviews(db: Session) -> PersonResolutionReviewService:
    session_factory = session_factory_for(db)
    return PersonResolutionReviewService(SqlAlchemyPersonPersistence(session_factory))


@router.get("/person-resolution/reviews", response_model=list[ResolutionReviewView])
def list_person_resolution_reviews(
    limit: int = Query(default=50, ge=1, le=500),
    db: Session = Depends(get_db),  # noqa: B008
) -> list[ResolutionReviewView]:
    """Pending ER v2 decisions with their structured candidate comparison."""
    return _person_resolution_reviews(db).list_pending(db, limit=limit)


@router.get(
    "/person-resolution/reviews/{decision_id}",
    response_model=ResolutionReviewView,
    responses={404: {}},
)
def get_person_resolution_review(
    decision_id: int,
    db: Session = Depends(get_db),  # noqa: B008
) -> ResolutionReviewView:
    try:
        return _person_resolution_reviews(db).get(db, decision_id)
    except ResolutionReviewNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post(
    "/person-resolution/reviews/{decision_id}/decision",
    response_model=ResolutionReviewResult,
    responses={404: {}, 409: {}},
)
def apply_person_resolution_review(
    decision_id: int,
    body: PersonResolutionReviewDecisionBody,
    db: Session = Depends(get_db),  # noqa: B008
) -> ResolutionReviewResult:
    """Apply an explicit reviewer action; MERGE_PERSONS is the only merge path."""
    try:
        result = _person_resolution_reviews(db).apply(
            db,
            decision_id,
            body.action,
            person_id=body.person_id,
            source_person_id=body.source_person_id,
            note=body.note,
        )
    except ResolutionReviewNotFoundError as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ResolutionReviewStateError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    db.commit()
    return result
