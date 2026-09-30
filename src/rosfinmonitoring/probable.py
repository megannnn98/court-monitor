"""A match that only the Airtable list knows about: probable, not confirmed.

The перечень is what the state publishes. The Airtable snapshot is a list an operator
curates, and a name in it is very likely in the перечень — but the state has not said
so, so the matcher must not report it as `MATCHED`, and the candidate query must not
treat the person as confirmed absent from the list either. Such a match is
`MATCHED_PROBABLE`: visible, reviewable, and never a confirmation.

Only names the *official* list leaves unmatched are considered, so a person the
published list already contains keeps its confirmed match and its row.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from db.orm_models import (
    RosfinMatchRecord,
    RosfinmonitoringEntryRecord,
    RosfinmonitoringSnapshotRecord,
)
from rosfinmonitoring.matcher import RuleBasedRosfinmonitoringMatcher
from rosfinmonitoring.matcher_models import RosfinMatchStatus
from rosfinmonitoring.models import AIRTABLE_SNAPSHOT_SOURCE_URL

logger = logging.getLogger("rosfinmonitoring")

INSERT_CHUNK = 500
PROBABLE_REASON = "в перечень попал только по списку Airtable: подтверждения нет"


class AirtableProvisionalMatcher:
    """Marks, for the people the official list leaves out, the ones Airtable's list has.

    Runs after the official check and only downgrades a confirmed absence, so it can
    never weaken a match the published list produced. Safe to run repeatedly: the rows
    it owns for a snapshot are rewritten whole, and a person who left Airtable's list
    goes back to being simply unmatched rather than keeping a stale probability.
    """

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        matcher: RuleBasedRosfinmonitoringMatcher | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._matcher = matcher or RuleBasedRosfinmonitoringMatcher(session_factory)

    def apply(self, official_snapshot_id: int) -> int:
        """(how many people are probable matches, having been absent officially).

        Returns 0 when there is no Airtable list, or no official snapshot to judge
        against: without the official answer there is nothing to downgrade.
        """
        airtable_id = self._airtable_snapshot_id()
        if airtable_id is None:
            return 0
        with self._session_factory() as session:
            absent = {
                person_id
                for person_id in session.scalars(
                    select(RosfinMatchRecord.person_id).where(
                        RosfinMatchRecord.snapshot_id == official_snapshot_id,
                        RosfinMatchRecord.status == RosfinMatchStatus.NOT_MATCHED.value,
                    )
                )
            }
            # An operator who empties the list must not leave yesterday's probabilities
            # standing, so the snapshot is looked up whether or not it still has entries
            # and the rows are rewritten either way.
            empty = not session.scalar(
                select(func.count(RosfinmonitoringEntryRecord.id)).where(
                    RosfinmonitoringEntryRecord.snapshot_id == airtable_id
                )
            )
        if not absent or empty:
            self._clear(airtable_id)
            return 0

        rows: list[dict[str, object]] = []
        for person_id in sorted(absent):
            result = self._matcher.match_person(person_id, airtable_id)
            # Only a confirmed hit in Airtable's list is a probability worth storing.
            if result.status is not RosfinMatchStatus.MATCHED:
                continue
            rows.append(
                {
                    "person_id": person_id,
                    "snapshot_id": airtable_id,
                    "status": RosfinMatchStatus.MATCHED_PROBABLE.value,
                    "confidence": result.confidence,
                    "matched_entry_id": result.matched_entry_id,
                    "matched_entry_name": result.matched_entry_name,
                    "candidate_entries": [
                        entry.model_dump(mode="json") for entry in result.candidate_entries
                    ],
                    "reasons": [PROBABLE_REASON, *result.reasons],
                    # When the observation was made, not when the row is rewritten.
                    "matched_at": result.matched_at or datetime.now(UTC),
                    "matcher_name": self._matcher.matcher_name,
                    "matcher_version": self._matcher.matcher_version,
                }
            )
        self._write(airtable_id, rows)
        logger.info(
            "event=rf_airtable_probable_matches snapshot_id=%d absent=%d probable=%d",
            airtable_id,
            len(absent),
            len(rows),
        )
        return len(rows)

    def _airtable_snapshot_id(self) -> int | None:
        """The Airtable-sourced snapshot, whether or not it still has entries."""
        with self._session_factory() as session:
            return session.scalar(
                select(RosfinmonitoringSnapshotRecord.id)
                .where(RosfinmonitoringSnapshotRecord.source_url == AIRTABLE_SNAPSHOT_SOURCE_URL)
                .order_by(RosfinmonitoringSnapshotRecord.id.desc())
                .limit(1)
            )

    def _write(self, snapshot_id: int, rows: list[dict[str, object]]) -> None:
        """This snapshot's probable rows, rewritten whole: a name that left Airtable's
        list stops being probable instead of staying so forever."""
        with self._session_factory.begin() as session:
            session.execute(
                delete(RosfinMatchRecord).where(
                    RosfinMatchRecord.snapshot_id == snapshot_id,
                    RosfinMatchRecord.status == RosfinMatchStatus.MATCHED_PROBABLE.value,
                )
            )
            for start in range(0, len(rows), INSERT_CHUNK):
                session.execute(insert(RosfinMatchRecord), rows[start : start + INSERT_CHUNK])

    def _clear(self, snapshot_id: int) -> None:
        with self._session_factory.begin() as session:
            session.execute(
                delete(RosfinMatchRecord).where(
                    RosfinMatchRecord.snapshot_id == snapshot_id,
                    RosfinMatchRecord.status == RosfinMatchStatus.MATCHED_PROBABLE.value,
                )
            )


def probable_person_ids(session: Session, snapshot_id: int, person_ids: Sequence[int]) -> set[int]:
    """Of `person_ids`, those with a probable (Airtable-only) match in `snapshot_id`."""
    if not person_ids:
        return set()
    return set(
        session.scalars(
            select(RosfinMatchRecord.person_id).where(
                RosfinMatchRecord.snapshot_id == snapshot_id,
                RosfinMatchRecord.status == RosfinMatchStatus.MATCHED_PROBABLE.value,
                RosfinMatchRecord.person_id.in_(person_ids),
            )
        )
    )
