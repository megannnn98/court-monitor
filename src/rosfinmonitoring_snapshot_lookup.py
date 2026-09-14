"""PostgreSQL lookup of the latest imported Rosfinmonitoring snapshot."""

from __future__ import annotations

from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session, sessionmaker

from orm_models import (
    RosfinMatchRecord,
    RosfinmonitoringEntryRecord,
    RosfinmonitoringSnapshotRecord,
)
from research_workflow.models import RosfinmonitoringSnapshotSummary


class SqlAlchemyRosfinmonitoringSnapshotLookup:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def latest_imported_snapshot(self) -> RosfinmonitoringSnapshotSummary | None:
        """Latest snapshot (by snapshot_date, then id) that has imported entries.

        A snapshot row is created before its entries are saved, so a snapshot
        without entries is an incomplete import and is never selected.
        """
        entry_count = (
            select(func.count(RosfinmonitoringEntryRecord.id))
            .where(RosfinmonitoringEntryRecord.snapshot_id == RosfinmonitoringSnapshotRecord.id)
            .scalar_subquery()
        )
        match_count = (
            select(func.count(RosfinMatchRecord.id))
            .where(RosfinMatchRecord.snapshot_id == RosfinmonitoringSnapshotRecord.id)
            .scalar_subquery()
        )
        query = (
            select(
                RosfinmonitoringSnapshotRecord.id,
                RosfinmonitoringSnapshotRecord.snapshot_date,
                entry_count,
                match_count,
            )
            .where(
                exists().where(
                    RosfinmonitoringEntryRecord.snapshot_id == RosfinmonitoringSnapshotRecord.id
                )
            )
            .order_by(
                RosfinmonitoringSnapshotRecord.snapshot_date.desc(),
                RosfinmonitoringSnapshotRecord.id.desc(),
            )
            .limit(1)
        )
        with self._session_factory() as session:
            row = session.execute(query).first()
        if row is None:
            return None
        snapshot_id, snapshot_date, entries, matches = row
        return RosfinmonitoringSnapshotSummary(
            snapshot_id=snapshot_id,
            snapshot_date=snapshot_date,
            entry_count=entries,
            match_count=matches,
        )
