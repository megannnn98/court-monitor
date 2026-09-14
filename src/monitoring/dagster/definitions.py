"""Dagster code location: `dagster dev -m monitoring.dagster.definitions` (with src on PYTHONPATH)."""

from __future__ import annotations

import dagster as dg

from monitoring.dagster.assets import MONITORING_ASSETS
from monitoring.dagster.jobs import build_derived_job, monitoring_job
from monitoring.dagster.resources import MONITORING_RESOURCE_KEY, monitoring_service_resource
from monitoring.dagster.schedules import build_source_schedules
from monitoring.models import MonitoringSettings


def build_definitions(
    settings: MonitoringSettings,
    *,
    monitoring: object | None = None,
    derived_retry_delay_seconds: float | None = None,
) -> dg.Definitions:
    """`monitoring`: a prebuilt MonitoringService (tests); default builds one from env per run."""
    derived_job = (
        build_derived_job()
        if derived_retry_delay_seconds is None
        else build_derived_job(retry_delay_seconds=derived_retry_delay_seconds)
    )
    return dg.Definitions(
        assets=MONITORING_ASSETS,
        jobs=[monitoring_job, derived_job],
        schedules=build_source_schedules(settings),
        resources={
            MONITORING_RESOURCE_KEY: monitoring_service_resource()
            if monitoring is None
            else dg.ResourceDefinition.hardcoded_resource(monitoring),
            # Stage outputs are per-run handles: never persisted, never shared between runs.
            "io_manager": dg.mem_io_manager,
        },
    )


defs = build_definitions(MonitoringSettings.from_env())
