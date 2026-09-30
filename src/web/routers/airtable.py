"""Synchronizing the reference lists from Airtable: `POST /api/admin/airtable/sync`.

The handler names a table and hands the work to `AirtableSyncService`; it never calls
Airtable itself. Two runs at once are refused with 409 by an advisory lock in the
database, not by the disabled button in the browser.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from airtable.config import AirtableConfigurationError, AirtableSettings
from airtable.models import SyncReport
from airtable.service import AirtableSyncAlreadyRunningError, AirtableSyncService
from web.dependencies import get_db, session_factory_for
from web.response_models import AirtableSyncResponse, AirtableTableSyncResponse

router = APIRouter()


def _response(report: SyncReport) -> AirtableSyncResponse:
    return AirtableSyncResponse(
        status=report.status,
        started_at=report.started_at.isoformat(),
        finished_at=report.finished_at.isoformat() if report.finished_at else None,
        duration_seconds=round(report.duration_seconds, 3),
        tables={
            name: AirtableTableSyncResponse(
                created=result.created,
                updated=result.updated,
                unchanged=result.unchanged,
                errors=result.errors,
                received=result.received,
                error=result.error,
            )
            for name, result in report.tables.items()
        },
    )


@router.post(
    "/api/admin/airtable/sync",
    response_model=AirtableSyncResponse,
    responses={409: {}, 503: {}},
)
def sync_airtable(db: Session = Depends(get_db)) -> AirtableSyncResponse:  # noqa: B008
    """Bring the four Airtable lists into PostgreSQL and report what each one did.

    Synchronous and on the request thread: the lists are small enough to read in one
    pass, and the operator wants the result on the page they pressed the button on.
    """
    try:
        settings = AirtableSettings.from_env()
    except AirtableConfigurationError as exc:
        # Not a server fault: nothing is set up to sync from.
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    service = AirtableSyncService(session_factory_for(db), settings)
    try:
        report = service.sync()
    except AirtableSyncAlreadyRunningError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    # A table that failed is inside the report, with the others' results beside it.
    return _response(report)


@router.get("/api/admin/airtable/configured")
def airtable_configured() -> dict[str, object]:
    """Whether Airtable is set up. Never the token, and nothing else worth hiding."""
    return {"configured": AirtableSettings.is_configured()}
