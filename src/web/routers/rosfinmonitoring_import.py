"""Importing a published Rosfinmonitoring list: `POST /api/admin/rosfinmonitoring/import`.

The site at fedsfm.ru is the usual source, and step 5 of the pipeline fetches it. When
it cannot be reached, an operator downloads the page by hand and brings the file here,
so the list can be refreshed without anyone editing anything in the database.

The file arrives as the raw request body rather than as `multipart/form-data`: the bytes
are all that is needed, and staying off multipart keeps `python-multipart` — a heavy
addition to the dependency layer, since it would rebuild the image — out of the project.
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from db.orm_models import RosfinmonitoringSnapshotRecord
from rosfinmonitoring.ingestion import RosfinmonitoringIngestionPipeline
from rosfinmonitoring.parser import HtmlRosfinmonitoringParser
from rosfinmonitoring.persistence import (
    RosfinmonitoringPersistence,
    compute_content_hash,
)
from web.dependencies import get_db, session_factory_for
from web.response_models import RosfinmonitoringImportResponse

router = APIRouter()

# Where the list is published. Stored with the snapshot so a later run can tell where a
# given list came from, whatever route it arrived by.
RF_LIST_URL = "https://www.fedsfm.ru/documents/terrorists-catalog-portal-act"

# The published page is ~4 MB. A ceiling well above it catches a wrong upload (a video,
# an archive) without letting a fat request exhaust the API worker.
MAX_UPLOAD_BYTES = 64 * 1024 * 1024


@router.post(
    "/api/admin/rosfinmonitoring/import",
    response_model=RosfinmonitoringImportResponse,
    responses={413: {}, 422: {}},
)
async def import_rosfinmonitoring(
    request: Request,
    db: Session = Depends(get_db),  # noqa: B008
) -> RosfinmonitoringImportResponse:
    """Bring an uploaded list file in as a new snapshot.

    Idempotent by content: the same file twice leaves one snapshot. Unchanged content is
    reported rather than written again, so an accidental re-upload costs nothing.
    """
    raw = await request.body()
    if not raw.strip():
        raise HTTPException(status_code=422, detail="Файл пуст")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Файл больше {MAX_UPLOAD_BYTES // (1024 * 1024)} МБ — это не страница перечня",
        )
    session_factory = session_factory_for(db)
    persistence = RosfinmonitoringPersistence(session_factory)
    existing = _existing_snapshot(session_factory, compute_content_hash(raw))
    if existing is not None:
        # The same list is already here. Reported rather than written again: a list is
        # recognised by its content, so an accidental re-upload costs nothing and a
        # second copy of the same people never appears.
        return RosfinmonitoringImportResponse(
            status="unchanged",
            snapshot_id=existing.id,
            snapshot_date=existing.snapshot_date.isoformat(),
            entries=existing.entry_count,
            source_url=existing.source_url,
            detail="Перечень не изменился — в базе уже есть такой снимок",
        )
    pipeline = RosfinmonitoringIngestionPipeline(persistence, HtmlRosfinmonitoringParser())
    try:
        result = pipeline.ingest(raw, source_url=RF_LIST_URL)
    except ValueError as exc:
        # The pipeline raises for a file it cannot read a single person out of. That is
        # the operator's answer about their file, not a server fault.
        return RosfinmonitoringImportResponse(
            status="error", source_url=RF_LIST_URL, detail=str(exc)
        )
    return RosfinmonitoringImportResponse(
        status="imported",
        snapshot_id=result.snapshot_id,
        snapshot_date=_snapshot_date(session_factory, result.snapshot_id),
        entries=result.entries_created,
        source_url=RF_LIST_URL,
        rematch_required=True,
    )


def _existing_snapshot(
    session_factory: sessionmaker[Session], content_hash: str
) -> RosfinmonitoringSnapshotRecord | None:
    """The snapshot this exact file already became, if there is one."""
    with session_factory() as session:
        return session.scalar(
            select(RosfinmonitoringSnapshotRecord).where(
                RosfinmonitoringSnapshotRecord.content_hash == content_hash
            )
        )


def _snapshot_date(session_factory: sessionmaker[Session], snapshot_id: int) -> str | None:
    """When the imported snapshot is dated, as the candidate page shows it."""
    with session_factory() as session:
        record = session.get(RosfinmonitoringSnapshotRecord, snapshot_id)
    return record.snapshot_date.isoformat() if record is not None else None
