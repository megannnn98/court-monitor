"""Monitoring jobs.

`monitoring_job` runs the whole asset graph for one source in one process
(in-process executor, in-memory IO): stage outputs are run handles that must
never be shared between concurrent runs of different sources. Consequently
Dagster "re-execute from failure" / single-step re-execution is not supported
(the in-memory handle of the failed process is gone): launch the job again —
a full rerun is safe because every stage re-selects its work from PostgreSQL.

`monitoring_derived_job` re-runs classification, RF matching, semantic indexing
and findings without web access. It is the only place with a Dagster retry
policy: the source layer already retries HTTP, and a retryable failure of the
derived stages (e.g. Qdrant unavailable) is safe to repeat because every stage
selects only still-stale work.
"""

import dagster as dg

from monitoring.dagster.assets import MONITORING_ASSETS
from monitoring.models import FailureKind, MonitoringRunStatus
from monitoring.service import MonitoringService

MONITORING_JOB = "monitoring_job"
MONITORING_DERIVED_JOB = "monitoring_derived_job"
SOURCE_TAG = "monitoring/source"
DERIVED_TAG = "monitoring/derived"
DERIVED_MAX_RETRIES = 3
DERIVED_RETRY_DELAY_SECONDS = 60


class RetryableMonitoringFailure(Exception):
    """A derived run finished with retryable item failures (e.g. semantic index unavailable)."""


monitoring_job = dg.define_asset_job(
    name=MONITORING_JOB,
    selection=dg.AssetSelection.assets(*MONITORING_ASSETS),
    executor_def=dg.in_process_executor,
    description=(
        "Discover → ingest → extract → ER v2 → classify → RF → semantic → findings. "
        "Re-execute from failure is not supported: launch a full run (safe to repeat)."
    ),
)


def build_derived_job(
    *, retry_delay_seconds: float = DERIVED_RETRY_DELAY_SECONDS
) -> dg.JobDefinition:
    @dg.op(
        retry_policy=dg.RetryPolicy(
            max_retries=DERIVED_MAX_RETRIES,
            delay=retry_delay_seconds,
            backoff=dg.Backoff.EXPONENTIAL,
        )
    )
    def run_derived_monitoring(
        context: dg.OpExecutionContext, monitoring: dg.ResourceParam[MonitoringService]
    ) -> dg.Output[int]:
        run = monitoring.run_derived()
        details = monitoring.repository.get_run_details(run.id)
        retryable = [
            item
            for item in (details.items if details is not None else [])
            if item.failure_kind is FailureKind.RETRYABLE
        ]
        context.log.info(
            f"monitoring derived run {run.id}: {run.status.value}, "
            f"{len(retryable)} retryable failures"
        )
        run_failure_kind = run.stage_metrics.get("run", {}).get("failure_kind")
        if run.status is MonitoringRunStatus.FAILED and run_failure_kind != FailureKind.RETRYABLE:
            # A bug or misconfiguration fails the same way on every attempt: no retry.
            raise dg.Failure(
                description=f"monitoring run {run.id} failed: {run.error_message}",
                allow_retries=False,
            )
        if run.status is MonitoringRunStatus.FAILED or retryable:
            raise RetryableMonitoringFailure(
                f"monitoring run {run.id} {run.status.value}: "
                f"{len(retryable)} retryable failures ({run.error_message or 'see run items'})"
            )
        return dg.Output(
            run.id,
            metadata={
                "monitoring_run_id": run.id,
                "status": run.status.value,
                "classifications_created": run.classifications_created,
                "rf_matches_created": run.rf_matches_created,
                "semantic_entities_indexed": run.semantic_entities_indexed,
                "findings_created": run.findings_created,
                "error_count": run.error_count,
            },
        )

    @dg.job(
        name=MONITORING_DERIVED_JOB,
        executor_def=dg.in_process_executor,
        tags={DERIVED_TAG: "true"},
        description="Classification, RF matching, semantic indexing and findings (no web access)",
    )
    def monitoring_derived_job() -> None:
        run_derived_monitoring()

    return monitoring_derived_job
