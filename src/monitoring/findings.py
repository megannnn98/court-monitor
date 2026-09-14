"""Monitoring findings: persisted, deduplicated actionable results (ADR 0013).

Which persons satisfy a criterion is decided by `CandidateQueryService` — the
same query `list-candidates`, the API and research use — never by SQL here.
This module only remembers when and in which run a person first/last matched.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol

from sqlalchemy import and_, func, literal_column, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from candidates.models import DEFAULT_MIN_PERSECUTION_CONFIDENCE, RosfinmonitoringStatus
from candidates.service import CandidateQueryService
from db.orm_models import (
    MonitoringFindingRecord,
    PersecutionClassificationRecord,
    RosfinMatchRecord,
)
from monitoring.models import MonitoringFindingStatus, MonitoringFindingView
from persecution.queries import latest_persecution_classification_ids

logger = logging.getLogger("monitoring")

POLITICAL_NOT_IN_RF = "political_persecution_not_in_rf"
ENBV_CRITERIA_VERSION = "enbv-v1"
NO_RF_SNAPSHOT = "no_rf_snapshot"


@dataclass(frozen=True)
class FindingCriterion:
    finding_type: str
    criteria_version: str
    min_persecution_confidence: float = DEFAULT_MIN_PERSECUTION_CONFIDENCE
    # Only a confirmed absence: AMBIGUOUS / NEEDS_REVIEW / INSUFFICIENT_DATA /
    # never-matched are not "not in Rosfinmonitoring".
    include_rf_statuses: frozenset[RosfinmonitoringStatus] = frozenset(
        {RosfinmonitoringStatus.NOT_MATCHED}
    )


class MonitoringQueryProvider(Protocol):
    def criteria(self) -> Sequence[FindingCriterion]: ...


class DefaultMonitoringQueryProvider:
    """The product use case: politically persecuted AND confirmed absent from RF."""

    def criteria(self) -> Sequence[FindingCriterion]:
        return (
            FindingCriterion(
                finding_type=POLITICAL_NOT_IN_RF, criteria_version=ENBV_CRITERIA_VERSION
            ),
        )


@dataclass
class CriterionEvaluation:
    finding_type: str
    criteria_version: str
    matched: int = 0
    created: int = 0
    reactivated: int = 0
    deactivated: int = 0


@dataclass
class FindingEvaluation:
    snapshot_id: int | None
    skipped_reason: str | None = None
    criteria: list[CriterionEvaluation] = field(default_factory=list)

    @property
    def created(self) -> int:
        return sum(item.created for item in self.criteria)


class MonitoringFindingService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        candidate_query: CandidateQueryService | None = None,
        query_provider: MonitoringQueryProvider | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._candidate_query = candidate_query or CandidateQueryService(session_factory)
        self._query_provider = query_provider or DefaultMonitoringQueryProvider()

    def evaluate(self, *, run_id: int, snapshot_id: int | None) -> FindingEvaluation:
        """Record which persons satisfy each criterion now.

        Without a Rosfinmonitoring snapshot nothing can be "not in RF": the
        evaluation is skipped and existing findings are left untouched.
        """
        if snapshot_id is None:
            logger.warning(
                "monitoring_findings_skipped run_id=%s reason=%s", run_id, NO_RF_SNAPSHOT
            )
            return FindingEvaluation(snapshot_id=None, skipped_reason=NO_RF_SNAPSHOT)
        evaluation = FindingEvaluation(snapshot_id=snapshot_id)
        for criterion in self._query_provider.criteria():
            with self._session_factory.begin() as session:
                evaluation.criteria.append(
                    self._evaluate_criterion(session, criterion, run_id, snapshot_id)
                )
        return evaluation

    def _evaluate_criterion(
        self,
        session: Session,
        criterion: FindingCriterion,
        run_id: int,
        snapshot_id: int,
    ) -> CriterionEvaluation:
        result = self._candidate_query.get_candidates(
            snapshot_id,
            min_persecution_confidence=criterion.min_persecution_confidence,
            limit=None,
            include_rf_statuses=criterion.include_rf_statuses,
            session=session,
        )
        person_ids = sorted({candidate.person_id for candidate in result.candidates})
        outcome = CriterionEvaluation(
            finding_type=criterion.finding_type,
            criteria_version=criterion.criteria_version,
            matched=len(person_ids),
        )
        classification_ids = self._latest_classification_ids(session, person_ids)
        match_ids = self._rf_match_ids(session, person_ids, snapshot_id)
        inactive_before = set(
            session.scalars(
                select(MonitoringFindingRecord.person_id).where(
                    MonitoringFindingRecord.finding_type == criterion.finding_type,
                    MonitoringFindingRecord.criteria_version == criterion.criteria_version,
                    MonitoringFindingRecord.active.is_(False),
                )
            ).all()
        )

        for person_id in person_ids:
            statement = insert(MonitoringFindingRecord).values(
                finding_type=criterion.finding_type,
                person_id=person_id,
                criteria_version=criterion.criteria_version,
                status=MonitoringFindingStatus.OPEN.value,
                active=True,
                first_seen_run_id=run_id,
                first_seen_at=func.now(),
                last_seen_run_id=run_id,
                last_seen_at=func.now(),
                snapshot_id=snapshot_id,
                persecution_classification_id=classification_ids.get(person_id),
                rosfin_match_id=match_ids.get(person_id),
            )
            # first_seen_* are never updated: they answer "since when, since which run".
            inserted: bool = session.execute(
                statement.on_conflict_do_update(
                    constraint="uq_monitoring_findings_type_person_criteria",
                    set_={
                        "active": True,
                        "inactive_since": None,
                        "last_seen_run_id": run_id,
                        "last_seen_at": func.now(),
                        "snapshot_id": snapshot_id,
                        "persecution_classification_id": classification_ids.get(person_id),
                        "rosfin_match_id": match_ids.get(person_id),
                        "updated_at": func.now(),
                    },
                ).returning(literal_column("xmax = 0").label("inserted"))
            ).scalar_one()
            if inserted:
                outcome.created += 1
            elif person_id in inactive_before:
                outcome.reactivated += 1
        outcome.deactivated = self._deactivate_missing(session, criterion, person_ids)
        logger.info(
            "monitoring_findings_evaluated run_id=%s finding_type=%s criteria_version=%s "
            "snapshot_id=%s matched=%d created=%d reactivated=%d deactivated=%d",
            run_id,
            criterion.finding_type,
            criterion.criteria_version,
            snapshot_id,
            outcome.matched,
            outcome.created,
            outcome.reactivated,
            outcome.deactivated,
        )
        return outcome

    @staticmethod
    def _deactivate_missing(
        session: Session, criterion: FindingCriterion, person_ids: Sequence[int]
    ) -> int:
        query = (
            update(MonitoringFindingRecord)
            .where(
                MonitoringFindingRecord.finding_type == criterion.finding_type,
                MonitoringFindingRecord.criteria_version == criterion.criteria_version,
                MonitoringFindingRecord.active.is_(True),
            )
            .values(active=False, inactive_since=func.now(), updated_at=func.now())
            .returning(MonitoringFindingRecord.id)
        )
        if person_ids:
            query = query.where(MonitoringFindingRecord.person_id.not_in(list(person_ids)))
        return len(session.scalars(query).all())

    @staticmethod
    def _latest_classification_ids(session: Session, person_ids: Sequence[int]) -> dict[int, int]:
        if not person_ids:
            return {}
        rows = session.execute(
            select(
                PersecutionClassificationRecord.person_id, PersecutionClassificationRecord.id
            ).where(
                PersecutionClassificationRecord.id.in_(latest_persecution_classification_ids()),
                PersecutionClassificationRecord.person_id.in_(list(person_ids)),
            )
        ).all()
        return {person_id: classification_id for person_id, classification_id in rows}

    @staticmethod
    def _rf_match_ids(
        session: Session, person_ids: Sequence[int], snapshot_id: int
    ) -> dict[int, int]:
        if not person_ids:
            return {}
        rows = session.execute(
            select(RosfinMatchRecord.person_id, RosfinMatchRecord.id).where(
                and_(
                    RosfinMatchRecord.snapshot_id == snapshot_id,
                    RosfinMatchRecord.person_id.in_(list(person_ids)),
                )
            )
        ).all()
        return {person_id: match_id for person_id, match_id in rows}

    def list_findings(
        self, *, active_only: bool = True, limit: int = 100
    ) -> list[MonitoringFindingView]:
        query = (
            select(MonitoringFindingRecord)
            .order_by(
                MonitoringFindingRecord.first_seen_at.desc(), MonitoringFindingRecord.id.desc()
            )
            .limit(limit)
        )
        if active_only:
            query = query.where(MonitoringFindingRecord.active.is_(True))
        with self._session_factory() as session:
            return [
                MonitoringFindingView(
                    id=record.id,
                    finding_type=record.finding_type,
                    person_id=record.person_id,
                    criteria_version=record.criteria_version,
                    status=MonitoringFindingStatus(record.status),
                    active=record.active,
                    first_seen_run_id=record.first_seen_run_id,
                    first_seen_at=record.first_seen_at,
                    last_seen_run_id=record.last_seen_run_id,
                    last_seen_at=record.last_seen_at,
                    inactive_since=record.inactive_since,
                    snapshot_id=record.snapshot_id,
                    persecution_classification_id=record.persecution_classification_id,
                    rosfin_match_id=record.rosfin_match_id,
                )
                for record in session.scalars(query).all()
            ]
