"""One schedule per enabled source (MONITORING_ENABLED_SOURCES, MONITORING_CRON).

Schedules start STOPPED: turning one on is an explicit operator action in the
Dagster UI, so `dagster dev` never starts scraping by itself.
"""

from __future__ import annotations

import dagster as dg

from monitoring.dagster.jobs import SOURCE_TAG, monitoring_job
from monitoring.models import MonitoringSettings, MonitoringTrigger


def build_source_schedules(settings: MonitoringSettings) -> list[dg.ScheduleDefinition]:
    return [
        dg.ScheduleDefinition(
            name=f"monitoring_{source.replace('-', '_')}_schedule",
            cron_schedule=settings.cron,
            job=monitoring_job,
            run_config=dg.RunConfig(
                ops={
                    "source_discovery": {
                        "config": {"source": source, "trigger": MonitoringTrigger.SCHEDULE.value}
                    }
                }
            ),
            tags={SOURCE_TAG: source},
            default_status=dg.DefaultScheduleStatus.STOPPED,
            description=f"Regular monitoring of {source}",
        )
        for source in settings.enabled_sources
    ]
