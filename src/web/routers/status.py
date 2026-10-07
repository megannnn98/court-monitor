"""The legacy status strip and menu counters for the React console. Read-only."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from web.dependencies import get_db
from web.response_models import StatusResponse
from web.status import read_status

router = APIRouter()


@router.get("/status", response_model=StatusResponse)
def get_status(db: Session = Depends(get_db)) -> StatusResponse:  # noqa: B008
    """Publications, people, the result, the operator's queue, the latest monitoring run,
    the step running now and the next thing to do."""
    return read_status(db)
