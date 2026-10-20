"""Criminal cases formed from the extracted events. Read-only; run `resolve-cases` to form them."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from cases import read
from cases.read import CaseDetail, CaseSummary
from web.dependencies import get_db

router = APIRouter()


class CaseListResponse(BaseModel):
    total: int
    items: list[CaseSummary]


@router.get("/cases", response_model=CaseListResponse)
def list_cases(
    person_key: str | None = Query(default=None, max_length=255),
    ambiguous: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),  # noqa: B008
) -> CaseListResponse:
    """Cases, newest first. `person_key` lists the cases of one person (`entity_groups.key`);
    `ambiguous` only those opened apart from a case they matched."""
    if person_key is not None:
        items = read.cases_of_person(db, person_key)
        if ambiguous:
            items = [case for case in items if case.ambiguous]
        return CaseListResponse(total=len(items), items=items[offset : offset + limit])
    total, items = read.list_cases(db, limit=limit, offset=offset, ambiguous_only=ambiguous)
    return CaseListResponse(total=total, items=items)


@router.get("/cases/{case_id}", response_model=CaseDetail)
def get_case(case_id: UUID, db: Session = Depends(get_db)) -> CaseDetail:  # noqa: B008
    """A case with its events, the reasons each was joined (score, features, the cases it
    was not joined to), its participants and its publications."""
    detail = read.case_detail(db, case_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Case not found")
    return detail
