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

from datetime import timedelta
from typing import Any

from sqlalchemy import Select, Subquery, and_, func, or_, select, union_all
from sqlalchemy.orm import Session, sessionmaker

from db.orm_models import (
    PersecutionClassificationRecord,
    PersonAliasRecord,
    PersonEventLinkRecord,
    PersonRecord,
    PersonResolutionDecisionRecord,
    RosfinMatchRecord,
)
from persons.models import PersonStatus

# Longer than any ER / review transaction plus clock skew between hosts.
EVIDENCE_SETTLE_INTERVAL = timedelta(minutes=10)


def _person_change_stamps(*, settle: timedelta) -> Subquery:
    """(person_id, changed_at): newest evidence change per person, plus the settle interval."""
    decision = PersonResolutionDecisionRecord
    parts: list[Select[Any]] = [
        select(PersonRecord.id.label("person_id"), PersonRecord.created_at.label("changed_at")),
        select(PersonRecord.id, PersonRecord.updated_at).where(
            PersonRecord.updated_at.is_not(None)
        ),
        select(PersonEventLinkRecord.person_id, PersonEventLinkRecord.created_at),
        select(PersonAliasRecord.person_id, PersonAliasRecord.created_at),
        select(decision.selected_person_id, decision.created_at).where(
            decision.selected_person_id.is_not(None)
        ),
        select(decision.selected_person_id, decision.reviewed_at).where(
            decision.selected_person_id.is_not(None), decision.reviewed_at.is_not(None)
        ),
    ]
    stamps = union_all(*parts).subquery()
    return (
        select(
            stamps.c.person_id,
            (func.max(stamps.c.changed_at) + settle).label("changed_at"),
        )
        .group_by(stamps.c.person_id)
        .subquery()
    )


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
        changed = _person_change_stamps(settle=self._settle)
        classification = PersecutionClassificationRecord
        query = (
            select(PersonRecord.id)
            .join(changed, changed.c.person_id == PersonRecord.id)
            .outerjoin(
                classification,
                and_(
                    classification.person_id == PersonRecord.id,
                    classification.classifier_name == classifier_name,
                    classification.classifier_version == classifier_version,
                ),
            )
            .where(
                PersonRecord.status == PersonStatus.ACTIVE.value,
                or_(
                    classification.id.is_(None),
                    classification.classified_at < changed.c.changed_at,
                ),
            )
            .order_by(PersonRecord.id)
        )
        with self._session_factory() as session:
            return list(session.scalars(query).all())

    def persons_pending_rf_match(
        self, *, snapshot_id: int, matcher_name: str, matcher_version: str
    ) -> list[int]:
        """Active persons without a match for the snapshot, with a match older than their
        evidence, or with a match produced by another matcher (version) — including rows
        written before the version was recorded (NULL)."""
        changed = _person_change_stamps(settle=self._settle)
        query = (
            select(PersonRecord.id)
            .join(changed, changed.c.person_id == PersonRecord.id)
            .outerjoin(
                RosfinMatchRecord,
                and_(
                    RosfinMatchRecord.person_id == PersonRecord.id,
                    RosfinMatchRecord.snapshot_id == snapshot_id,
                ),
            )
            .where(
                PersonRecord.status == PersonStatus.ACTIVE.value,
                or_(
                    RosfinMatchRecord.id.is_(None),
                    RosfinMatchRecord.matched_at < changed.c.changed_at,
                    RosfinMatchRecord.matcher_name.is_distinct_from(matcher_name),
                    RosfinMatchRecord.matcher_version.is_distinct_from(matcher_version),
                ),
            )
            .order_by(PersonRecord.id)
        )
        with self._session_factory() as session:
            return list(session.scalars(query).all())
