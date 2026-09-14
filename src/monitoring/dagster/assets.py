"""Monitoring asset graph. Each asset calls one `MonitoringService` stage and publishes
its counters as metadata; assets pass run handles and IDs, never article bodies.

No `from __future__ import annotations` here: Dagster inspects the annotations.

If a stage raises (an infrastructure failure of the whole stage), the monitoring
run is finished as FAILED before the exception reaches Dagster.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import dagster as dg

from monitoring.models import MonitoringAlreadyRunningError, MonitoringTrigger
from monitoring.service import (
    DiscoveryResult,
    IngestionResult,
    MonitoringService,
    RunHandle,
    StageResult,
)

GROUP = "monitoring"


class MonitoringRunConfig(dg.Config):
    source: str
    # None: MONITORING_DISCOVERY_LIMIT.
    discovery_limit: int | None = None
    backfill: bool = False
    refetch_known: bool = False
    trigger: str = MonitoringTrigger.MANUAL.value


@dataclass(frozen=True)
class DiscoveryStep:
    """`handle` is None when the source was already being monitored (run skipped)."""

    handle: RunHandle | None
    discovery: DiscoveryResult = field(default_factory=DiscoveryResult)
    skipped_reason: str | None = None


@dataclass(frozen=True)
class IngestionStep:
    handle: RunHandle | None
    ingestion: IngestionResult = field(default_factory=IngestionResult)


@dataclass(frozen=True)
class RfStep:
    handle: RunHandle | None
    snapshot_id: int | None = None


def _guard[T](monitoring: MonitoringService, handle: RunHandle, stage: Callable[[], T]) -> T:
    try:
        return stage()
    except Exception as exc:
        monitoring.finish(handle, error=exc)
        raise


def _stage_metadata(handle: RunHandle, result: StageResult) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "monitoring_run_id": handle.run_id,
        "source": handle.source or "",
        "processed": result.processed,
        "created": result.created,
        "skipped": result.skipped,
        "failed": result.failed,
        "reviews": result.reviews,
    }
    if result.metrics:
        metadata["details"] = dg.MetadataValue.json(result.metrics)
    return metadata


def _skipped_output[T](value: T) -> dg.Output[T]:
    return dg.Output(value, metadata={"skipped": "source already being monitored"})


@dg.asset(group_name=GROUP, description="Discover references of one source; starts the run.")
def source_discovery(
    config: MonitoringRunConfig, monitoring: dg.ResourceParam[MonitoringService]
) -> dg.Output[DiscoveryStep]:
    trigger = MonitoringTrigger.BACKFILL if config.backfill else MonitoringTrigger(config.trigger)
    try:
        handle = monitoring.start_source_run(
            config.source,
            trigger=trigger,
            discovery_limit=config.discovery_limit,
            refetch_known=config.refetch_known,
        )
    except MonitoringAlreadyRunningError as exc:
        return dg.Output(
            DiscoveryStep(handle=None, skipped_reason=str(exc)),
            metadata={"skipped": str(exc), "running_monitoring_run_id": exc.running_run_id or 0},
        )
    discovery = _guard(monitoring, handle, lambda: monitoring.discover(handle))
    known = 0 if handle.refetch_known else len(discovery.known_external_ids)
    return dg.Output(
        DiscoveryStep(handle=handle, discovery=discovery),
        metadata=_stage_metadata(
            handle,
            StageResult(processed=len(discovery.references), skipped=known),
        ),
    )


@dg.asset(group_name=GROUP, description="Fetch and store new documents.")
def source_ingestion(
    source_discovery: DiscoveryStep, monitoring: dg.ResourceParam[MonitoringService]
) -> dg.Output[IngestionStep]:
    handle = source_discovery.handle
    if handle is None:
        return _skipped_output(IngestionStep(handle=None))
    ingestion = _guard(
        monitoring, handle, lambda: monitoring.ingest(handle, source_discovery.discovery)
    )
    return dg.Output(
        IngestionStep(handle=handle, ingestion=ingestion),
        metadata=_stage_metadata(
            handle,
            StageResult(
                processed=len(ingestion.article_ids) + ingestion.failed,
                created=len(ingestion.article_ids),
                failed=ingestion.failed,
            ),
        ),
    )


@dg.asset(group_name=GROUP, description="Extract mentions and events of pending articles.")
def entity_extraction(
    source_ingestion: IngestionStep, monitoring: dg.ResourceParam[MonitoringService]
) -> dg.Output[RunHandle | None]:
    handle = source_ingestion.handle
    if handle is None:
        return _skipped_output(None)
    result = _guard(
        monitoring, handle, lambda: monitoring.extract(handle, source_ingestion.ingestion)
    )
    return dg.Output(handle, metadata=_stage_metadata(handle, result))


@dg.asset(group_name=GROUP, description="Entity Resolution v2 of undecided person mentions.")
def person_resolution(
    entity_extraction: RunHandle | None, monitoring: dg.ResourceParam[MonitoringService]
) -> dg.Output[RunHandle | None]:
    handle = entity_extraction
    if handle is None:
        return _skipped_output(None)
    result = _guard(monitoring, handle, lambda: monitoring.resolve(handle))
    return dg.Output(handle, metadata=_stage_metadata(handle, result))


@dg.asset(group_name=GROUP, description="Classify persons whose evidence changed.")
def persecution_classification(
    person_resolution: RunHandle | None, monitoring: dg.ResourceParam[MonitoringService]
) -> dg.Output[RunHandle | None]:
    handle = person_resolution
    if handle is None:
        return _skipped_output(None)
    result = _guard(monitoring, handle, lambda: monitoring.classify(handle))
    return dg.Output(handle, metadata=_stage_metadata(handle, result))


@dg.asset(group_name=GROUP, description="Match changed persons against the latest RF snapshot.")
def rosfinmonitoring_matching(
    persecution_classification: RunHandle | None, monitoring: dg.ResourceParam[MonitoringService]
) -> dg.Output[RfStep]:
    handle = persecution_classification
    if handle is None:
        return _skipped_output(RfStep(handle=None))
    snapshot_id = _guard(monitoring, handle, lambda: monitoring.match_rosfinmonitoring(handle))
    run = monitoring.repository.get_run(handle.run_id)
    rf_metrics = {} if run is None else run.stage_metrics.get("rf_matching", {})
    return dg.Output(
        RfStep(handle=handle, snapshot_id=snapshot_id),
        metadata={
            **_stage_metadata(
                handle,
                StageResult(
                    processed=sum(rf_metrics.get("statuses", {}).values()),
                    failed=int(rf_metrics.get("failed", 0)),
                    metrics=rf_metrics,
                ),
            ),
            "rf_snapshot_id": snapshot_id if snapshot_id is not None else "none (no snapshot)",
        },
    )


@dg.asset(group_name=GROUP, description="Incremental semantic index update (derived index).")
def semantic_indexing(
    rosfinmonitoring_matching: RfStep, monitoring: dg.ResourceParam[MonitoringService]
) -> dg.Output[RfStep]:
    handle = rosfinmonitoring_matching.handle
    if handle is None:
        return _skipped_output(rosfinmonitoring_matching)
    # A Qdrant outage is recorded on the run by the stage and does not raise.
    result = _guard(monitoring, handle, lambda: monitoring.index_semantic(handle))
    return dg.Output(rosfinmonitoring_matching, metadata=_stage_metadata(handle, result))


@dg.asset(group_name=GROUP, description="Evaluate findings and finish the monitoring run.")
def monitoring_summary(
    semantic_indexing: RfStep, monitoring: dg.ResourceParam[MonitoringService]
) -> dg.MaterializeResult[None]:
    handle = semantic_indexing.handle
    if handle is None:
        return dg.MaterializeResult(metadata={"skipped": "source already being monitored"})
    _guard(
        monitoring,
        handle,
        lambda: monitoring.evaluate_findings(handle, snapshot_id=semantic_indexing.snapshot_id),
    )
    run = monitoring.finish(handle)
    return dg.MaterializeResult(
        metadata={
            "monitoring_run_id": run.id,
            "source": run.source or "",
            "status": run.status.value,
            "duration_seconds": run.duration_seconds or 0.0,
            "documents_discovered": run.documents_discovered,
            "documents_ingested": run.documents_ingested,
            "documents_skipped": run.documents_skipped,
            "documents_failed": run.documents_failed,
            "persons_created": run.persons_created,
            "persons_linked": run.persons_linked,
            "person_reviews_created": run.person_reviews_created,
            "classifications_created": run.classifications_created,
            "rf_matches_created": run.rf_matches_created,
            "semantic_entities_indexed": run.semantic_entities_indexed,
            "findings_created": run.findings_created,
            "error_count": run.error_count,
            "stage_metrics": dg.MetadataValue.json(run.stage_metrics),
        }
    )


MONITORING_ASSETS = [
    source_discovery,
    source_ingestion,
    entity_extraction,
    person_resolution,
    persecution_classification,
    rosfinmonitoring_matching,
    semantic_indexing,
    monitoring_summary,
]
