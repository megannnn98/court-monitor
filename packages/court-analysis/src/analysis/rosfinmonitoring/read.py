"""Snapshot inspection and inclusion-date browsing, independent of HTTP and ORM callers."""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from analysis.storage.models import (
    EntityGroupRfMatchRecord,
    RosfinmonitoringEntryRecord,
    RosfinmonitoringSnapshotRecord,
)
from db.arrays import any_of


@dataclass(frozen=True)
class Snapshot:
    id: int
    snapshot_date: datetime
    source_url: str
    content_hash: str
    entry_count: int


def _snapshot(record: RosfinmonitoringSnapshotRecord) -> Snapshot:
    return Snapshot(
        record.id, record.snapshot_date, record.source_url, record.content_hash, record.entry_count
    )


def snapshots(session: Session, *, limit: int) -> list[Snapshot]:
    """Snapshot history ordered by its publication date, including operator snapshots."""
    return [
        _snapshot(row)
        for row in session.scalars(
            select(RosfinmonitoringSnapshotRecord)
            .order_by(RosfinmonitoringSnapshotRecord.snapshot_date.desc())
            .limit(limit)
        )
    ]


def snapshot(session: Session, snapshot_id: int) -> Snapshot | None:
    row = session.get(RosfinmonitoringSnapshotRecord, snapshot_id)
    return None if row is None else _snapshot(row)


@dataclass(frozen=True)
class SnapshotEntry:
    id: int
    snapshot_id: int
    full_name: str
    normalized_name: str
    matching_key: str
    birth_date: datetime | None
    inclusion_reason: str | None
    birth_place: str | None
    inclusion_date: datetime | None
    inclusion_source: str | None


def _entry(row: RosfinmonitoringEntryRecord) -> SnapshotEntry:
    return SnapshotEntry(
        row.id,
        row.snapshot_id,
        row.full_name,
        row.normalized_name,
        row.matching_key,
        row.birth_date,
        row.inclusion_reason,
        row.birth_place,
        row.inclusion_date,
        row.inclusion_source,
    )


def snapshot_entries(
    session: Session, snapshot_id: int, *, limit: int, offset: int
) -> list[SnapshotEntry]:
    """Stable id pagination of the contents of a snapshot."""
    return [
        _entry(row)
        for row in session.scalars(
            select(RosfinmonitoringEntryRecord)
            .where(RosfinmonitoringEntryRecord.snapshot_id == snapshot_id)
            .order_by(RosfinmonitoringEntryRecord.id)
            .offset(offset)
            .limit(limit)
        )
    ]


def _moment(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=UTC)


def included_entries(
    session: Session,
    snapshot_id: int,
    *,
    start: date | None,
    end: date | None,
    limit: int | None,
) -> list[SnapshotEntry]:
    """Inclusion-date browsing; an open period shows only entries with known dates."""
    query = select(RosfinmonitoringEntryRecord).where(
        RosfinmonitoringEntryRecord.snapshot_id == snapshot_id,
    )
    if start is not None:
        query = query.where(RosfinmonitoringEntryRecord.inclusion_date >= _moment(start))
    if end is not None:
        query = query.where(
            RosfinmonitoringEntryRecord.inclusion_date < _moment(end + date.resolution)
        )
    if start is None and end is None:
        query = query.where(RosfinmonitoringEntryRecord.inclusion_date.isnot(None))
    query = query.order_by(
        RosfinmonitoringEntryRecord.inclusion_date.desc().nulls_last(),
        RosfinmonitoringEntryRecord.full_name,
    )
    if limit is not None:
        query = query.limit(limit)
    return [_entry(row) for row in session.scalars(query)]


def inclusion_counts(
    session: Session, snapshot_id: int, *, start: date | None, end: date | None
) -> tuple[int, int, int]:
    """(within period, with any inclusion date, all entries), as the list page counts."""
    query = (
        select(func.count())
        .select_from(RosfinmonitoringEntryRecord)
        .where(
            RosfinmonitoringEntryRecord.snapshot_id == snapshot_id,
            RosfinmonitoringEntryRecord.inclusion_date.isnot(None),
        )
    )
    dated = session.scalar(query) or 0
    if start is not None:
        query = query.where(RosfinmonitoringEntryRecord.inclusion_date >= _moment(start))
    if end is not None:
        query = query.where(
            RosfinmonitoringEntryRecord.inclusion_date < _moment(end + date.resolution)
        )
    in_period = session.scalar(query) or 0
    total = (
        session.scalar(
            select(func.count())
            .select_from(RosfinmonitoringEntryRecord)
            .where(RosfinmonitoringEntryRecord.snapshot_id == snapshot_id)
        )
        or 0
    )
    return in_period, dated, total


@dataclass(frozen=True)
class EntryMatch:
    entry_id: int
    group_id: int
    level: str


def entry_matches(session: Session, ids: Collection[int]) -> list[EntryMatch]:
    """Matches for the displayed entries only; full matches precede namesakes."""
    if not ids:
        return []
    return [
        EntryMatch(*row)
        for row in session.execute(
            select(
                EntityGroupRfMatchRecord.entry_id,
                EntityGroupRfMatchRecord.group_id,
                EntityGroupRfMatchRecord.level,
            )
            .where(any_of(EntityGroupRfMatchRecord.entry_id, ids))
            .order_by((EntityGroupRfMatchRecord.level == "full").desc())
        )
    ]


_REMOVED = """
    FROM (
        SELECT DISTINCT ON (o.normalized_name, o.birth_date)
               o.full_name, o.birth_date, o.birth_place, o.added_on
        FROM rfm_operator_entries o
        WHERE o.removed
          AND NOT EXISTS (
              SELECT 1 FROM rosfinmonitoring_entries e
              WHERE e.snapshot_id = (SELECT max(id) FROM rosfinmonitoring_snapshots)
                AND e.normalized_name = o.normalized_name
                AND e.birth_date::date = o.birth_date)
        ORDER BY o.normalized_name, o.birth_date, o.added_on DESC NULLS LAST
    ) gone
"""


@dataclass(frozen=True)
class RemovedEntry:
    full_name: str
    birth_date: date | None
    birth_place: str
    added_on: date | None


def removed_count(session: Session) -> int:
    """Operator entries marked removed and absent from the latest stored snapshot."""
    return session.scalar(text("SELECT count(*)" + _REMOVED)) or 0


def removed_entries(session: Session, *, limit: int) -> list[RemovedEntry]:
    return [
        RemovedEntry(*row)
        for row in session.execute(
            text(
                "SELECT full_name, birth_date, birth_place, added_on"
                + _REMOVED
                + "ORDER BY added_on DESC NULLS LAST, full_name LIMIT :limit"
            ),
            {"limit": limit},
        )
    ]
