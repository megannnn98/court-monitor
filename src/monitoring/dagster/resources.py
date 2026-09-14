"""Dagster resources: the composition root's `MonitoringService`, built lazily per run process.

Nothing secret is Dagster config: DATABASE_URL, QDRANT_URL and the rest come from
the environment, so the Dagster UI never shows them.
"""

from __future__ import annotations

import dagster as dg

from application import build_application_services
from monitoring.service import MonitoringService

MONITORING_RESOURCE_KEY = "monitoring"


def monitoring_service_resource() -> dg.ResourceDefinition:
    def create(_: dg.InitResourceContext) -> MonitoringService:
        return build_application_services().monitoring

    return dg.ResourceDefinition(
        resource_fn=create,
        description="MonitoringService from application.build_application_services()",
    )
