"""Rosfinmonitoring snapshots and entries."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from db.orm_models import (
    RosfinmonitoringEntryRecord,
    RosfinmonitoringSnapshotRecord,
)
from web.dependencies import get_db
from web.response_models import RosfinmonitoringEntryResponse, RosfinmonitoringSnapshotResponse

router = APIRouter()


# Rosfinmonitoring endpoints
@router.get(
    "/rosfinmonitoring/snapshots",
    response_model=list[RosfinmonitoringSnapshotResponse],
)
def list_rosfinmonitoring_snapshots(
    limit: int = Query(default=10, ge=1, le=100),
    db: Session = Depends(get_db),  # noqa: B008
) -> list[RosfinmonitoringSnapshotResponse]:
    """List Rosfinmonitoring snapshots."""
    snapshots = db.scalars(
        select(RosfinmonitoringSnapshotRecord)
        .order_by(RosfinmonitoringSnapshotRecord.snapshot_date.desc())
        .limit(limit)
    ).all()

    return [
        RosfinmonitoringSnapshotResponse(
            id=s.id,
            snapshot_date=s.snapshot_date.isoformat(),
            source_url=s.source_url,
            content_hash=s.content_hash,
            entry_count=s.entry_count,
        )
        for s in snapshots
    ]


@router.get(
    "/rosfinmonitoring/snapshots/{snapshot_id}",
    response_model=RosfinmonitoringSnapshotResponse,
)
def get_rosfinmonitoring_snapshot(
    snapshot_id: int,
    db: Session = Depends(get_db),  # noqa: B008
) -> RosfinmonitoringSnapshotResponse:
    """Get a Rosfinmonitoring snapshot by ID."""
    snapshot = db.get(RosfinmonitoringSnapshotRecord, snapshot_id)

    if not snapshot:
        raise HTTPException(status_code=404, detail="Snapshot not found")

    return RosfinmonitoringSnapshotResponse(
        id=snapshot.id,
        snapshot_date=snapshot.snapshot_date.isoformat(),
        source_url=snapshot.source_url,
        content_hash=snapshot.content_hash,
        entry_count=snapshot.entry_count,
    )


@router.get(
    "/rosfinmonitoring/snapshots/{snapshot_id}/entries",
    response_model=list[RosfinmonitoringEntryResponse],
)
def list_rosfinmonitoring_entries(
    snapshot_id: int,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),  # noqa: B008
) -> list[RosfinmonitoringEntryResponse]:
    """List entries in a Rosfinmonitoring snapshot."""
    snapshot = db.get(RosfinmonitoringSnapshotRecord, snapshot_id)
    if not snapshot:
        raise HTTPException(status_code=404, detail="Snapshot not found")

    entries = db.scalars(
        select(RosfinmonitoringEntryRecord)
        .where(RosfinmonitoringEntryRecord.snapshot_id == snapshot_id)
        .order_by(RosfinmonitoringEntryRecord.id)
        .offset(offset)
        .limit(limit)
    ).all()

    return [
        RosfinmonitoringEntryResponse(
            id=e.id,
            snapshot_id=e.snapshot_id,
            full_name=e.full_name,
            normalized_name=e.normalized_name,
            matching_key=e.matching_key,
            birth_date=e.birth_date.isoformat() if e.birth_date else None,
            inclusion_reason=e.inclusion_reason,
        )
        for e in entries
    ]
