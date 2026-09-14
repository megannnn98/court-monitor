"""Persistence layer for Rosfinmonitoring match results."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from db.orm_models import RosfinMatchRecord
from rosfinmonitoring.matcher_models import (
    RosfinCandidateEntry,
    RosfinMatchResult,
    RosfinMatchStatus,
)


class RosfinMatchPersistence:
    """Persistence layer for Person ↔ Rosfinmonitoring match results."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def save_match_result(self, result: RosfinMatchResult) -> int:
        """Save a match result to the database.

        Returns the ID of the saved match record.
        """
        candidate_entries = [
            candidate.model_dump(mode="json") for candidate in result.candidate_entries
        ]
        matched_at = result.matched_at or datetime.now(UTC)
        values = {
            "person_id": result.person_id,
            "snapshot_id": result.snapshot_id,
            "status": result.status.value,
            "confidence": result.confidence,
            "matched_entry_id": result.matched_entry_id,
            "matched_entry_name": result.matched_entry_name,
            "candidate_entries": candidate_entries,
            "reasons": result.reasons,
            "matched_at": matched_at,
        }

        with self._session_factory.begin() as session:
            statement = (
                insert(RosfinMatchRecord)
                .values(**values)
                .on_conflict_do_update(
                    constraint="uq_rosfin_matches_person_snapshot",
                    set_={
                        "status": result.status.value,
                        "confidence": result.confidence,
                        "matched_entry_id": result.matched_entry_id,
                        "matched_entry_name": result.matched_entry_name,
                        "candidate_entries": candidate_entries,
                        "reasons": result.reasons,
                        "matched_at": matched_at,
                    },
                )
                .returning(RosfinMatchRecord.id)
            )
            match_id = session.execute(statement).scalar_one()
            return int(match_id)

    def get_match_result(
        self,
        person_id: int,
        snapshot_id: int,
    ) -> RosfinMatchResult | None:
        """Retrieve a match result from the database."""
        with self._session_factory() as session:
            record = session.scalar(
                select(RosfinMatchRecord).where(
                    RosfinMatchRecord.person_id == person_id,
                    RosfinMatchRecord.snapshot_id == snapshot_id,
                )
            )

            if record is None:
                return None

            candidate_entries = [
                RosfinCandidateEntry.model_validate(candidate)
                for candidate in record.candidate_entries
            ]

            return RosfinMatchResult(
                person_id=record.person_id,
                snapshot_id=record.snapshot_id,
                status=RosfinMatchStatus(record.status),
                confidence=record.confidence,
                matched_entry_id=record.matched_entry_id,
                matched_entry_name=record.matched_entry_name,
                candidate_entries=candidate_entries,
                reasons=record.reasons,
                matched_at=record.matched_at,
            )

    def list_matches_for_snapshot(
        self,
        snapshot_id: int,
        status: RosfinMatchStatus | None = None,
    ) -> list[RosfinMatchResult]:
        """List all match results for a snapshot, optionally filtered by status."""
        with self._session_factory() as session:
            query = select(RosfinMatchRecord).where(RosfinMatchRecord.snapshot_id == snapshot_id)

            if status is not None:
                query = query.where(RosfinMatchRecord.status == status.value)

            records = session.scalars(query).all()

            results: list[RosfinMatchResult] = []
            for record in records:
                candidate_entries = [
                    RosfinCandidateEntry.model_validate(candidate)
                    for candidate in record.candidate_entries
                ]

                results.append(
                    RosfinMatchResult(
                        person_id=record.person_id,
                        snapshot_id=record.snapshot_id,
                        status=RosfinMatchStatus(record.status),
                        confidence=record.confidence,
                        matched_entry_id=record.matched_entry_id,
                        matched_entry_name=record.matched_entry_name,
                        candidate_entries=candidate_entries,
                        reasons=record.reasons,
                        matched_at=record.matched_at,
                    )
                )

            return results

    def delete_match_result(
        self,
        person_id: int,
        snapshot_id: int,
    ) -> bool:
        """Delete a match result from the database.

        Returns True if a record was deleted, False if no record was found.
        """
        with self._session_factory() as session:
            record = session.scalar(
                select(RosfinMatchRecord).where(
                    RosfinMatchRecord.person_id == person_id,
                    RosfinMatchRecord.snapshot_id == snapshot_id,
                )
            )

            if record is None:
                return False

            session.delete(record)
            session.commit()
            return True
