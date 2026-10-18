"""Which persons the person-based analysis still has to classify or match, decided
from PostgreSQL state. This is the compatibility path of the monitoring run; entity
groups are analysed whole by their own steps.

A person's derived results (classification, Rosfinmonitoring match) are stale when that person's evidence changed after the result was
written. Evidence change is the newest of: person created/updated, event
linked, alias added, ER decision recorded or reviewed for the person.

Evidence timestamps are transaction start times (`now()`) or application
clocks, not commit times: a result computed while another run's ER transaction
was still open can be written *after* that transaction's timestamps yet without
seeing its rows. A result therefore only counts as fresh when it was written at
least `EVIDENCE_SETTLE_INTERVAL` after the last evidence change; a recently
changed person is recomputed once more (idempotently) by a later run.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from analysis.storage.models import PersecutionClassificationRecord, RosfinMatchRecord
from persons import read as person_read

# Longer than any ER / review transaction plus clock skew between hosts.
EVIDENCE_SETTLE_INTERVAL = timedelta(minutes=10)


def _older(written: datetime | None, changed: datetime | None) -> bool:
    """`written < changed` as SQL reads it: an unknown time is never older."""
    return written is not None and changed is not None and written < changed


class SqlAlchemyPendingPersonAnalysis:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        evidence_settle_interval: timedelta = EVIDENCE_SETTLE_INTERVAL,
    ) -> None:
        self._session_factory = session_factory
        self._settle = evidence_settle_interval

    def persons_pending_classification(
        self, *, classifier_name: str, classifier_version: str
    ) -> list[int]:
        with self._session_factory() as session:
            changed = person_read.active_evidence_changes(session, settle=self._settle)
            classified = dict(
                session.execute(
                    select(
                        PersecutionClassificationRecord.person_id,
                        PersecutionClassificationRecord.classified_at,
                    ).where(
                        PersecutionClassificationRecord.classifier_name == classifier_name,
                        PersecutionClassificationRecord.classifier_version == classifier_version,
                    )
                )
                .tuples()
                .all()
            )
        return [
            person_id
            for person_id, at in changed.items()
            if person_id not in classified or _older(classified[person_id], at)
        ]

    def persons_pending_rf_match(
        self, *, snapshot_id: int, matcher_name: str, matcher_version: str
    ) -> list[int]:
        """Active persons without a match for the snapshot, with a match older than their
        evidence, or with a match produced by another matcher (version) — including rows
        written before the version was recorded (NULL)."""
        with self._session_factory() as session:
            changed = person_read.active_evidence_changes(session, settle=self._settle)
            matches = {
                person_id: (matched_at, name, version)
                for person_id, matched_at, name, version in session.execute(
                    select(
                        RosfinMatchRecord.person_id,
                        RosfinMatchRecord.matched_at,
                        RosfinMatchRecord.matcher_name,
                        RosfinMatchRecord.matcher_version,
                    ).where(RosfinMatchRecord.snapshot_id == snapshot_id)
                ).all()
            }
        return [
            person_id
            for person_id, at in changed.items()
            if person_id not in matches
            or _older(matches[person_id][0], at)
            or matches[person_id][1] != matcher_name
            or matches[person_id][2] != matcher_version
        ]
