"""«О системе» for the React console: the build stamp and the totals. Read-only."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from web.about import read_about
from web.dependencies import get_db
from web.response_models import AboutResponse

router = APIRouter()


@router.get("/about", response_model=AboutResponse)
def get_about(db: Session = Depends(get_db)) -> AboutResponse:  # noqa: B008
    """The build this instance runs, the document and person totals, the last
    successful operator run."""
    return read_about(db)
