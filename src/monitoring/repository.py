"""PostgreSQL persistence of monitoring runs, failed items and source checkpoints.

Every method is its own short transaction: a monitoring run never holds one
transaction across stages, so a failure never rolls back finished work.
Timestamps come from the database clock (`now()`) so stale-run detection does
not depend on the clocks of several worker hosts.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import timedelta
from typing import Any

from sqlalchemy import func, literal, select, update
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from db.orm_models import (
    MonitoringFindingRecord,
    MonitoringRunItemRecord,
    MonitoringRunRecord,
    SourceMonitoringStateRecord,
)
from monitoring.models import (
    FailureKind,
    MonitoringAlreadyRunningError,
    MonitoringRunDetails,
    MonitoringRunItemView,
    MonitoringRunStatus,
    MonitoringRunView,
    MonitoringStage,
    MonitoringTrigger,
    SourceMonitoringStateView,
    classify_failure,
)

logger = logging.getLogger("monitoring")

RUN_COUNTERS = frozenset(
    {
        "documents_discovered",
        "documents_ingested",
        "documents_skipped",
        "documents_failed",
        "articles_extracted",
        "events_created",
        "persons_created",
        "persons_linked",
        "person_reviews_created",
        "classifications_created",
        "rf_matches_created",
        "semantic_entities_indexed",
        "findings_created",
    }
)
MAX_ERROR_MESSAGE_CHARS = 2000


def _error_text(error: BaseException) -> str:
    return f"{type(error).__name__}: {error}"[:MAX_ERROR_MESSAGE_CHARS]


def _run_view(record: MonitoringRunRecord) -> MonitoringRunView:
    duration = (
        None
        if record.finished_at is None
        else (record.finished_at - record.started_at).total_seconds()
    )
    return MonitoringRunView(
        id=record.id,
        scope=record.scope,
        source=record.source,
        trigger_type=MonitoringTrigger(record.trigger_type),
        status=MonitoringRunStatus(record.status),
        parameters=record.parameters,
        started_at=record.started_at,
        heartbeat_at=record.heartbeat_at,
        finished_at=record.finished_at,
        duration_seconds=duration,
        documents_discovered=record.documents_discovered,
        documents_ingested=record.documents_ingested,
        documents_skipped=record.documents_skipped,
        documents_failed=record.documents_failed,
        articles_extracted=record.articles_extracted,
        events_created=record.events_created,
        persons_created=record.persons_created,
        persons_linked=record.persons_linked,
        person_reviews_created=record.person_reviews_created,
        classifications_created=record.classifications_created,
        rf_matches_created=record.rf_matches_created,
        semantic_entities_indexed=record.semantic_entities_indexed,
        findings_created=record.findings_created,
        error_count=record.error_count,
        rf_snapshot_id=record.rf_snapshot_id,
        stage_metrics=record.stage_metrics,
        error_message=record.error_message,
    )


class SqlAlchemyMonitoringRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def start_run(
        self,
        *,
        scope: str,
        source: str | None,
        trigger: MonitoringTrigger,
        parameters: Mapping[str, Any],
        stale_after: timedelta,
    ) -> int:
        """Create a RUNNING run; at most one per scope (partial unique index).

        Runs whose heartbeat is older than `stale_after` lost their process and
        are aborted first, so a crash never blocks the scope forever.
        """
        self.abort_stale_runs(stale_after)
        try:
            with self._session_factory.begin() as session:
                run_id = session.execute(
                    insert(MonitoringRunRecord)
                    .values(
                        scope=scope,
                        source=source,
                        trigger_type=trigger.value,
                        status=MonitoringRunStatus.RUNNING.value,
                        parameters=dict(parameters),
                        started_at=func.now(),
                        heartbeat_at=func.now(),
                        stage_metrics={},
                        error_count=0,
                        **dict.fromkeys(RUN_COUNTERS, 0),
                    )
                    .returning(MonitoringRunRecord.id)
                ).scalar_one()
        except IntegrityError:
            with self._session_factory() as session:
                running_id = session.scalar(
                    select(MonitoringRunRecord.id).where(
                        MonitoringRunRecord.scope == scope,
                        MonitoringRunRecord.status == MonitoringRunStatus.RUNNING.value,
                    )
                )
            raise MonitoringAlreadyRunningError(scope, running_id) from None
        logger.info(
            "monitoring_run_started run_id=%s scope=%s trigger=%s", run_id, scope, trigger.value
        )
        return run_id

    def abort_stale_runs(self, stale_after: timedelta) -> list[int]:
        with self._session_factory.begin() as session:
            aborted = list(
                session.scalars(
                    update(MonitoringRunRecord)
                    .where(
                        MonitoringRunRecord.status == MonitoringRunStatus.RUNNING.value,
                        MonitoringRunRecord.heartbeat_at < func.now() - stale_after,
                    )
                    .values(
                        status=MonitoringRunStatus.ABORTED.value,
                        finished_at=func.now(),
                        error_message=(
                            f"stale: no heartbeat for {int(stale_after.total_seconds() // 60)} "
                            "minutes, process presumed dead"
                        ),
                    )
                    .returning(MonitoringRunRecord.id)
                ).all()
            )
        for run_id in aborted:
            logger.warning("monitoring_run_aborted_stale run_id=%s", run_id)
        return aborted

    def heartbeat(self, run_id: int) -> None:
        with self._session_factory.begin() as session:
            session.execute(
                update(MonitoringRunRecord)
                .where(MonitoringRunRecord.id == run_id)
                .values(heartbeat_at=func.now())
            )

    def add_counters(self, run_id: int, counters: Mapping[str, int]) -> None:
        unknown = set(counters) - RUN_COUNTERS
        if unknown:
            raise ValueError(f"Unknown monitoring counters: {sorted(unknown)}")
        values: dict[str, Any] = {
            name: getattr(MonitoringRunRecord, name) + amount
            for name, amount in counters.items()
            if amount
        }
        values["heartbeat_at"] = func.now()
        with self._session_factory.begin() as session:
            session.execute(
                update(MonitoringRunRecord).where(MonitoringRunRecord.id == run_id).values(values)
            )

    def set_stage_metrics(
        self, run_id: int, stage: MonitoringStage, metrics: Mapping[str, Any]
    ) -> None:
        with self._session_factory.begin() as session:
            session.execute(
                update(MonitoringRunRecord)
                .where(MonitoringRunRecord.id == run_id)
                .values(
                    stage_metrics=MonitoringRunRecord.stage_metrics.op("||")(
                        literal({stage.value: dict(metrics)}, JSONB)
                    ),
                    heartbeat_at=func.now(),
                )
            )

    def set_rf_snapshot(self, run_id: int, snapshot_id: int | None) -> None:
        with self._session_factory.begin() as session:
            session.execute(
                update(MonitoringRunRecord)
                .where(MonitoringRunRecord.id == run_id)
                .values(rf_snapshot_id=snapshot_id)
            )

    def record_failure(
        self,
        run_id: int,
        *,
        stage: MonitoringStage,
        entity_type: str,
        error: BaseException,
        entity_id: int | None = None,
        external_ref: str | None = None,
    ) -> FailureKind:
        kind = classify_failure(error)
        with self._session_factory.begin() as session:
            session.add(
                MonitoringRunItemRecord(
                    run_id=run_id,
                    stage=stage.value,
                    entity_type=entity_type,
                    entity_id=entity_id,
                    external_ref=external_ref,
                    status="failed",
                    failure_kind=kind.value,
                    error_type=type(error).__name__,
                    error_message=str(error)[:MAX_ERROR_MESSAGE_CHARS],
                )
            )
            session.execute(
                update(MonitoringRunRecord)
                .where(MonitoringRunRecord.id == run_id)
                .values(error_count=MonitoringRunRecord.error_count + 1, heartbeat_at=func.now())
            )
        logger.warning(
            "monitoring_item_failed run_id=%s stage=%s entity_type=%s entity_id=%s ref=%s "
            "kind=%s error_type=%s error=%s",
            run_id,
            stage.value,
            entity_type,
            entity_id,
            external_ref,
            kind.value,
            type(error).__name__,
            error,
        )
        return kind

    def finish_run(self, run_id: int, *, error: BaseException | None = None) -> MonitoringRunStatus:
        """Terminal status from the outcome; a run already aborted as stale stays aborted."""
        with self._session_factory.begin() as session:
            record = session.get(MonitoringRunRecord, run_id, with_for_update=True)
            if record is None:
                raise LookupError(f"Monitoring run {run_id} not found")
            if record.status != MonitoringRunStatus.RUNNING.value:
                return MonitoringRunStatus(record.status)
            error_message = record.error_message
            if error is not None:
                status = MonitoringRunStatus.FAILED
                error_message = _error_text(error)
            elif record.error_count:
                status = MonitoringRunStatus.COMPLETED_WITH_ERRORS
            else:
                status = MonitoringRunStatus.COMPLETED
            session.execute(
                update(MonitoringRunRecord)
                .where(MonitoringRunRecord.id == run_id)
                .values(
                    status=status.value,
                    error_message=error_message,
                    finished_at=func.now(),
                    heartbeat_at=func.now(),
                )
            )
        logger.info("monitoring_run_finished run_id=%s status=%s", run_id, status.value)
        return status

    def update_checkpoint(self, source: str, *, run_id: int, discovered_count: int) -> None:
        statement = insert(SourceMonitoringStateRecord).values(
            source_name=source,
            last_successful_run_id=run_id,
            last_successful_run_at=func.now(),
            last_discovered_at=func.now(),
            last_discovered_count=discovered_count,
            updated_at=func.now(),
        )
        statement = statement.on_conflict_do_update(
            index_elements=[SourceMonitoringStateRecord.source_name],
            set_={
                "last_successful_run_id": statement.excluded.last_successful_run_id,
                "last_successful_run_at": statement.excluded.last_successful_run_at,
                "last_discovered_at": statement.excluded.last_discovered_at,
                "last_discovered_count": statement.excluded.last_discovered_count,
                "updated_at": statement.excluded.updated_at,
            },
        )
        with self._session_factory.begin() as session:
            session.execute(statement)

    def get_run(self, run_id: int) -> MonitoringRunView | None:
        with self._session_factory() as session:
            record = session.get(MonitoringRunRecord, run_id)
            return None if record is None else _run_view(record)

    def get_run_details(self, run_id: int) -> MonitoringRunDetails | None:
        with self._session_factory() as session:
            record = session.get(MonitoringRunRecord, run_id)
            if record is None:
                return None
            items = session.scalars(
                select(MonitoringRunItemRecord)
                .where(MonitoringRunItemRecord.run_id == run_id)
                .order_by(MonitoringRunItemRecord.id)
            ).all()
            return MonitoringRunDetails(
                run=_run_view(record),
                items=[
                    MonitoringRunItemView(
                        id=item.id,
                        run_id=item.run_id,
                        stage=MonitoringStage(item.stage),
                        entity_type=item.entity_type,
                        entity_id=item.entity_id,
                        external_ref=item.external_ref,
                        status=item.status,
                        failure_kind=FailureKind(item.failure_kind),
                        error_type=item.error_type,
                        error_message=item.error_message,
                        created_at=item.created_at,
                    )
                    for item in items
                ],
            )

    def list_runs(
        self,
        *,
        limit: int = 20,
        source: str | None = None,
        status: MonitoringRunStatus | None = None,
    ) -> list[MonitoringRunView]:
        query = select(MonitoringRunRecord).order_by(MonitoringRunRecord.id.desc()).limit(limit)
        if source is not None:
            query = query.where(MonitoringRunRecord.source == source)
        if status is not None:
            query = query.where(MonitoringRunRecord.status == status.value)
        with self._session_factory() as session:
            return [_run_view(record) for record in session.scalars(query).all()]

    def list_source_states(self) -> list[SourceMonitoringStateView]:
        with self._session_factory() as session:
            records = session.scalars(
                select(SourceMonitoringStateRecord).order_by(
                    SourceMonitoringStateRecord.source_name
                )
            ).all()
            return [
                SourceMonitoringStateView(
                    source_name=record.source_name,
                    last_successful_run_id=record.last_successful_run_id,
                    last_successful_run_at=record.last_successful_run_at,
                    last_discovered_at=record.last_discovered_at,
                    last_discovered_count=record.last_discovered_count,
                    last_external_marker=record.last_external_marker,
                    updated_at=record.updated_at,
                )
                for record in records
            ]

    def count_active_findings(self) -> int:
        with self._session_factory() as session:
            return int(
                session.scalar(
                    select(func.count(MonitoringFindingRecord.id)).where(
                        MonitoringFindingRecord.active.is_(True)
                    )
                )
                or 0
            )
