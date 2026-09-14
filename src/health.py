"""Liveness and readiness (ADR 0014).

Liveness says only "the process answers". Readiness checks what the product
truth needs — PostgreSQL reachable and its schema at the Alembic head this code
was written for — and reports optional/derived dependencies separately:

- `unavailable` (HTTP 503): database down or schema not at head; nothing works.
- `degraded` (HTTP 200): structured research works, but a configured optional
  dependency (Qdrant) is down or monitoring has a stale run.
- `ready` (HTTP 200): everything configured is reachable.

Details never contain connection strings or exception messages (only types).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import timedelta
from enum import StrEnum
from functools import lru_cache
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker

from db.orm_models import MonitoringRunRecord
from monitoring.models import MonitoringRunStatus

logger = logging.getLogger("health")

REPO_ROOT = Path(__file__).resolve().parents[1]


class ComponentStatus(StrEnum):
    OK = "ok"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    NOT_CONFIGURED = "not_configured"


class ReadinessStatus(StrEnum):
    READY = "ready"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


class ComponentHealth(BaseModel):
    status: ComponentStatus
    detail: str | None = None


class ReadinessReport(BaseModel):
    status: ReadinessStatus
    components: dict[str, ComponentHealth] = Field(default_factory=dict)


class LivenessReport(BaseModel):
    status: str = "alive"


@lru_cache(maxsize=1)
def expected_schema_revision() -> str:
    """The single Alembic head shipped with this code."""
    script = ScriptDirectory.from_config(Config(str(REPO_ROOT / "alembic.ini")))
    heads = script.get_heads()
    if len(heads) != 1:
        raise RuntimeError(f"expected one Alembic head, found {len(heads)}")
    return heads[0]


class ReadinessChecker:
    # Required components: any non-OK status makes the service unavailable.
    REQUIRED = frozenset({"database", "schema"})

    def __init__(
        self,
        session_factory: sessionmaker[Session] | None,
        *,
        expected_revision: str,
        qdrant_probe: Callable[[], None] | None,
        together_configured: bool,
        stale_run_after: timedelta,
    ) -> None:
        self._session_factory = session_factory
        self._expected_revision = expected_revision
        self._qdrant_probe = qdrant_probe
        self._together_configured = together_configured
        self._stale_run_after = stale_run_after

    def check(self) -> ReadinessReport:
        components: dict[str, ComponentHealth] = {}
        components.update(self._database())
        components["semantic_retrieval"] = self._qdrant()
        components["natural_language_research"] = ComponentHealth(
            status=ComponentStatus.OK
            if self._together_configured
            else ComponentStatus.NOT_CONFIGURED,
            detail=None if self._together_configured else "TOGETHER_API_KEY/TOGETHER_MODEL not set",
        )
        if components["database"].status is ComponentStatus.OK:
            components["monitoring"] = self._monitoring()

        if any(components[name].status is not ComponentStatus.OK for name in self.REQUIRED):
            status = ReadinessStatus.UNAVAILABLE
        elif any(
            component.status in (ComponentStatus.DEGRADED, ComponentStatus.UNAVAILABLE)
            for component in components.values()
        ):
            status = ReadinessStatus.DEGRADED
        else:
            status = ReadinessStatus.READY
        return ReadinessReport(status=status, components=components)

    def _database(self) -> dict[str, ComponentHealth]:
        if self._session_factory is None:
            unavailable = ComponentHealth(
                status=ComponentStatus.UNAVAILABLE, detail="DATABASE_URL is not set"
            )
            return {"database": unavailable, "schema": unavailable}
        try:
            with self._session_factory() as session:
                session.execute(text("SELECT 1"))
                revision = session.scalar(text("SELECT version_num FROM alembic_version"))
        except Exception as exc:  # noqa: BLE001 - any failure means "not ready"
            logger.warning("event=readiness_database_failed error_kind=%s", type(exc).__name__)
            # The query may have failed on a missing alembic_version table.
            database_ok = self._ping()
            database = ComponentHealth(
                status=ComponentStatus.OK if database_ok else ComponentStatus.UNAVAILABLE,
                detail=None if database_ok else type(exc).__name__,
            )
            schema = ComponentHealth(
                status=ComponentStatus.UNAVAILABLE,
                detail="schema not migrated" if database_ok else "database unavailable",
            )
            return {"database": database, "schema": schema}
        if revision != self._expected_revision:
            schema = ComponentHealth(
                status=ComponentStatus.UNAVAILABLE,
                detail=f"schema revision {revision}, expected {self._expected_revision}: "
                "run alembic upgrade head",
            )
        else:
            schema = ComponentHealth(status=ComponentStatus.OK, detail=revision)
        return {"database": ComponentHealth(status=ComponentStatus.OK), "schema": schema}

    def _ping(self) -> bool:
        assert self._session_factory is not None
        try:
            with self._session_factory() as session:
                session.execute(text("SELECT 1"))
        except Exception:  # noqa: BLE001
            return False
        return True

    def _qdrant(self) -> ComponentHealth:
        if self._qdrant_probe is None:
            return ComponentHealth(
                status=ComponentStatus.NOT_CONFIGURED, detail="QDRANT_URL not set"
            )
        try:
            self._qdrant_probe()
        except Exception as exc:  # noqa: BLE001 - derived index: degraded, not down
            logger.warning("event=readiness_qdrant_failed error_kind=%s", type(exc).__name__)
            return ComponentHealth(
                status=ComponentStatus.UNAVAILABLE,
                detail=f"{type(exc).__name__}: structured research still works",
            )
        return ComponentHealth(status=ComponentStatus.OK)

    def _monitoring(self) -> ComponentHealth:
        assert self._session_factory is not None
        try:
            with self._session_factory() as session:
                stale = session.scalar(
                    select(func.count(MonitoringRunRecord.id)).where(
                        MonitoringRunRecord.status == MonitoringRunStatus.RUNNING.value,
                        MonitoringRunRecord.heartbeat_at < func.now() - self._stale_run_after,
                    )
                )
        except Exception as exc:  # noqa: BLE001
            return ComponentHealth(status=ComponentStatus.UNAVAILABLE, detail=type(exc).__name__)
        if stale:
            return ComponentHealth(
                status=ComponentStatus.DEGRADED,
                detail=f"{stale} stale running monitoring run(s); the next run aborts them",
            )
        return ComponentHealth(status=ComponentStatus.OK)


def qdrant_probe(qdrant_url: str, *, timeout_seconds: int = 2) -> Callable[[], None]:
    def probe() -> None:
        from qdrant_client import QdrantClient

        client = QdrantClient(url=qdrant_url, timeout=timeout_seconds, check_compatibility=False)
        try:
            client.get_collections()
        finally:
            client.close()

    return probe
