"""PostgreSQL lookup of the Rosfinmonitoring snapshots: the official list, and Airtable's."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel
from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session, sessionmaker

from db.orm_models import (
    RosfinMatchRecord,
    RosfinmonitoringEntryRecord,
    RosfinmonitoringSnapshotRecord,
)
from rosfinmonitoring.models import AIRTABLE_SNAPSHOT_SOURCE_URL


class RosfinmonitoringSnapshotSummary(BaseModel):
    snapshot_id: int
    snapshot_date: datetime
    # When the content was last seen as the published page: a list that has not changed
    # is seen again at every download.
    last_seen_at: datetime
    entry_count: int
    match_count: int


class SqlAlchemyRosfinmonitoringSnapshotLookup:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def latest_imported_snapshot(self) -> RosfinmonitoringSnapshotSummary | None:
        """The official list's latest snapshot (by snapshot_date, then id) with entries.

        A snapshot row is created before its entries are saved, so a snapshot without
        entries is an incomplete import and is never selected.

        The Airtable-sourced snapshot is deliberately not among the candidates, however
        recent it is: it is a list an operator curates, not the list the state publishes,
        and it must never become the answer to «who is in the перечень». A name that is
        in it alone is probable, and `rosfinmonitoring.probable` says so.
        """
        return self._latest(exclude_source=AIRTABLE_SNAPSHOT_SOURCE_URL)

    def airtable_snapshot(self) -> RosfinmonitoringSnapshotSummary | None:
        """The Airtable-sourced snapshot, if a sync has brought one in.

        Consulted only for probable matches; never the snapshot a candidate is judged
        against.
        """
        return self._latest(only_source=AIRTABLE_SNAPSHOT_SOURCE_URL)

    def _latest(
        self,
        *,
        exclude_source: str | None = None,
        only_source: str | None = None,
    ) -> RosfinmonitoringSnapshotSummary | None:
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
                RosfinmonitoringSnapshotRecord.fetched_at,
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
        if only_source is not None:
            query = query.where(RosfinmonitoringSnapshotRecord.source_url == only_source)
        if exclude_source is not None:
            query = query.where(RosfinmonitoringSnapshotRecord.source_url != exclude_source)
        with self._session_factory() as session:
            row = session.execute(query).first()
        if row is None:
            return None
        snapshot_id, snapshot_date, fetched_at, entries, matches = row
        return RosfinmonitoringSnapshotSummary(
            snapshot_id=snapshot_id,
            snapshot_date=snapshot_date,
            last_seen_at=max(snapshot_date, fetched_at),
            entry_count=entries,
            match_count=matches,
        )
