"""Persistence layer for Rosfinmonitoring data."""

from __future__ import annotations

import hashlib

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from db.orm_models import RosfinmonitoringEntryRecord, RosfinmonitoringSnapshotRecord
from rosfinmonitoring.models import (
    RosfinmonitoringEntry,
    RosfinmonitoringEntryStatus,
    RosfinmonitoringIngestionResult,
    RosfinmonitoringSnapshot,
)


class RosfinmonitoringPersistence:
    """Persistence layer for Rosfinmonitoring snapshots and entries."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def create_snapshot(self, snapshot: RosfinmonitoringSnapshot) -> int:
        """Create a new snapshot record in the database."""
        with self._session_factory() as session:
            record = RosfinmonitoringSnapshotRecord(
                snapshot_date=snapshot.snapshot_date,
                source_url=snapshot.source_url,
                content_hash=snapshot.content_hash,
                entry_count=snapshot.entry_count,
                fetched_at=snapshot.fetched_at,
            )
            session.add(record)
            session.commit()
            return record.id

    def snapshot_exists(self, content_hash: str) -> bool:
        """Check if a snapshot with the given content hash already exists."""
        with self._session_factory() as session:
            result = session.execute(
                select(RosfinmonitoringSnapshotRecord.id).where(
                    RosfinmonitoringSnapshotRecord.content_hash == content_hash
                )
            )
            return result.first() is not None

    def save_entries(
        self,
        snapshot_id: int,
        entries: list[RosfinmonitoringEntry],
    ) -> RosfinmonitoringIngestionResult:
        """Save entries for a snapshot, handling duplicates."""
        entries_created = 0
        entries_updated = 0
        skipped_duplicates = 0

        with self._session_factory() as session:
            for entry in entries:
                existing = session.execute(
                    select(RosfinmonitoringEntryRecord.id).where(
                        RosfinmonitoringEntryRecord.snapshot_id == snapshot_id,
                        RosfinmonitoringEntryRecord.full_name == entry.full_name,
                        RosfinmonitoringEntryRecord.birth_date == entry.birth_date,
                    )
                ).first()

                if existing:
                    skipped_duplicates += 1
                    continue

                record = RosfinmonitoringEntryRecord(
                    snapshot_id=snapshot_id,
                    full_name=entry.full_name,
                    normalized_name=entry.normalized_name,
                    matching_key=entry.matching_key,
                    birth_date=entry.birth_date,
                    birth_place=entry.birth_place,
                    snils=entry.snils,
                    inn=entry.inn,
                    inclusion_reason=entry.inclusion_reason,
                    inclusion_date=entry.inclusion_date,
                    status=entry.status,
                    raw_data=entry.raw_data,
                )
                session.add(record)
                entries_created += 1

            session.commit()

        return RosfinmonitoringIngestionResult(
            snapshot_id=snapshot_id,
            entries_created=entries_created,
            entries_updated=entries_updated,
            skipped_duplicates=skipped_duplicates,
        )

    def get_snapshot(self, snapshot_id: int) -> RosfinmonitoringSnapshot | None:
        """Get a snapshot by ID."""
        with self._session_factory() as session:
            record = session.execute(
                select(RosfinmonitoringSnapshotRecord).where(
                    RosfinmonitoringSnapshotRecord.id == snapshot_id
                )
            ).scalar_one_or_none()

            if not record:
                return None

            return RosfinmonitoringSnapshot(
                id=record.id,
                snapshot_date=record.snapshot_date,
                source_url=record.source_url,
                content_hash=record.content_hash,
                entry_count=record.entry_count,
                fetched_at=record.fetched_at,
            )

    def get_latest_snapshot(self) -> RosfinmonitoringSnapshot | None:
        """Get the most recent snapshot."""
        with self._session_factory() as session:
            record = session.execute(
                select(RosfinmonitoringSnapshotRecord).order_by(
                    RosfinmonitoringSnapshotRecord.snapshot_date.desc()
                )
            ).scalar_one_or_none()

            if not record:
                return None

            return RosfinmonitoringSnapshot(
                id=record.id,
                snapshot_date=record.snapshot_date,
                source_url=record.source_url,
                content_hash=record.content_hash,
                entry_count=record.entry_count,
                fetched_at=record.fetched_at,
            )

    def list_snapshots(self, limit: int = 10) -> list[RosfinmonitoringSnapshot]:
        """List recent snapshots."""
        with self._session_factory() as session:
            records = (
                session.execute(
                    select(RosfinmonitoringSnapshotRecord)
                    .order_by(RosfinmonitoringSnapshotRecord.snapshot_date.desc())
                    .limit(limit)
                )
                .scalars()
                .all()
            )

            return [
                RosfinmonitoringSnapshot(
                    id=record.id,
                    snapshot_date=record.snapshot_date,
                    source_url=record.source_url,
                    content_hash=record.content_hash,
                    entry_count=record.entry_count,
                    fetched_at=record.fetched_at,
                )
                for record in records
            ]

    def get_entries_for_snapshot(
        self,
        snapshot_id: int,
        limit: int = 1000,
        offset: int = 0,
    ) -> list[RosfinmonitoringEntry]:
        """Get entries for a specific snapshot."""
        with self._session_factory() as session:
            records = (
                session.execute(
                    select(RosfinmonitoringEntryRecord)
                    .where(RosfinmonitoringEntryRecord.snapshot_id == snapshot_id)
                    .limit(limit)
                    .offset(offset)
                )
                .scalars()
                .all()
            )

            return [
                RosfinmonitoringEntry(
                    id=record.id,
                    snapshot_id=record.snapshot_id,
                    full_name=record.full_name,
                    normalized_name=record.normalized_name,
                    matching_key=record.matching_key,
                    birth_date=record.birth_date,
                    birth_place=record.birth_place,
                    snils=record.snils,
                    inn=record.inn,
                    inclusion_reason=record.inclusion_reason,
                    inclusion_date=record.inclusion_date,
                    status=RosfinmonitoringEntryStatus(record.status),
                    raw_data=record.raw_data,
                )
                for record in records
            ]

    def find_entries_by_matching_key(
        self,
        snapshot_id: int,
        matching_key: str,
    ) -> list[RosfinmonitoringEntry]:
        """Find entries by matching key in a snapshot."""
        with self._session_factory() as session:
            records = (
                session.execute(
                    select(RosfinmonitoringEntryRecord).where(
                        RosfinmonitoringEntryRecord.snapshot_id == snapshot_id,
                        RosfinmonitoringEntryRecord.matching_key == matching_key,
                    )
                )
                .scalars()
                .all()
            )

            return [
                RosfinmonitoringEntry(
                    id=record.id,
                    snapshot_id=record.snapshot_id,
                    full_name=record.full_name,
                    normalized_name=record.normalized_name,
                    matching_key=record.matching_key,
                    birth_date=record.birth_date,
                    birth_place=record.birth_place,
                    snils=record.snils,
                    inn=record.inn,
                    inclusion_reason=record.inclusion_reason,
                    inclusion_date=record.inclusion_date,
                    status=RosfinmonitoringEntryStatus(record.status),
                    raw_data=record.raw_data,
                )
                for record in records
            ]


def compute_content_hash(raw_content: bytes) -> str:
    """Compute SHA256 hash of content."""
    return hashlib.sha256(raw_content).hexdigest()
