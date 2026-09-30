"""Synchronizing the reference lists: `POST /api/admin/airtable/sync`.

The handler names a list and hands the work to `AirtableSyncService`; it never reads
Airtable or a file itself. Two runs at once are refused with 409 by an advisory lock in
the database, not by the disabled button in the browser.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from airtable.config import AirtableConfigurationError
from airtable.files import FileTableClient, ImportSettings
from airtable.models import MODE_FILES, SyncReport
from airtable.service import (
    AirtableSyncAlreadyRunningError,
    build_sync_service,
    build_sync_source,
)
from web.dependencies import get_db, session_factory_for
from web.response_models import AirtableSyncResponse, AirtableTableSyncResponse

router = APIRouter()


def _response(report: SyncReport) -> AirtableSyncResponse:
    return AirtableSyncResponse(
        status=report.status,
        mode=report.mode,
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
                status=result.status,
                error=result.error,
                removed=result.removed,
                removed_blocked=result.removed_blocked,
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
    """Bring the four lists into PostgreSQL and report what each one did.

    Synchronous and on the request thread: the lists are small enough to read in one
    pass, and the operator wants the result on the page they pressed the button on.
    """
    try:
        service = build_sync_service(session_factory_for(db))
    except AirtableConfigurationError as exc:
        # Not a server fault: nothing is set up to sync from.
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    try:
        report = service.sync()
    except AirtableSyncAlreadyRunningError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    # A list that failed is inside the report, with the others' results beside it.
    return _response(report)


@router.get("/api/admin/airtable/configured")
def airtable_configured() -> dict[str, object]:
    """Whether a sync is possible and, if so, which way the records will arrive.

    Never the token, and nothing else worth hiding. `detail` names the exported files
    that were found, so an operator can see which lists are ready before pressing the
    button.
    """
    try:
        source = build_sync_source()
    except AirtableConfigurationError:
        return {"configured": False, "mode": None, "detail": None}
    detail: str | None = None
    if source.mode == MODE_FILES:
        found = FileTableClient(ImportSettings.from_env()).present()
        detail = ", ".join(f"{name}: {path.name}" for name, path in found.items()) or None
    return {"configured": True, "mode": source.mode, "detail": detail}
