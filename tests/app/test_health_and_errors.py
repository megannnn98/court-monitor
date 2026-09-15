"""Liveness/readiness, request ids and the API error model."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from api import app, get_readiness_checker, get_research_service
from health import ReadinessChecker, expected_schema_revision
from research.service import ResearchService


@pytest.fixture
def client() -> Iterator[TestClient]:
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


def _checker(
    session_factory: sessionmaker[Session] | None,
    *,
    revision: str | None = None,
    qdrant_down: bool = False,
    qdrant_configured: bool = True,
) -> ReadinessChecker:
    def probe() -> None:
        if qdrant_down:
            raise ConnectionError("connection refused to http://secret-host:6333")

    return ReadinessChecker(
        session_factory,
        expected_revision=revision or expected_schema_revision(),
        qdrant_probe=probe if qdrant_configured else None,
        together_configured=False,
        stale_run_after=timedelta(minutes=120),
    )


def test_liveness_needs_no_dependencies(client: TestClient) -> None:
    response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "alive"}


def test_ready_when_database_is_at_head(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    app.dependency_overrides[get_readiness_checker] = lambda: _checker(session_factory)

    response = client.get("/health/ready")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["components"]["database"]["status"] == "ok"
    assert body["components"]["schema"] == {"status": "ok", "detail": expected_schema_revision()}
    assert body["components"]["natural_language_research"]["status"] == "not_configured"
    assert body["components"]["monitoring"]["status"] == "ok"


def test_qdrant_outage_is_degraded_not_unavailable(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    app.dependency_overrides[get_readiness_checker] = lambda: _checker(
        session_factory, qdrant_down=True
    )

    response = client.get("/health/ready")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "degraded"
    assert body["components"]["semantic_retrieval"]["status"] == "unavailable"
    assert "secret-host" not in response.text


def test_schema_behind_head_is_unavailable(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    app.dependency_overrides[get_readiness_checker] = lambda: _checker(
        session_factory, revision="not_the_head"
    )

    response = client.get("/health/ready")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "unavailable"
    assert "run alembic upgrade head" in body["components"]["schema"]["detail"]


def test_missing_database_is_unavailable(client: TestClient) -> None:
    app.dependency_overrides[get_readiness_checker] = lambda: _checker(
        None, qdrant_configured=False
    )

    response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json()["components"]["database"]["status"] == "unavailable"


def test_stale_monitoring_run_is_degraded(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    with session_factory.begin() as session:
        session.execute(
            text(
                "INSERT INTO monitoring_runs (scope, source, trigger_type, status, started_at, "
                "heartbeat_at) VALUES ('source:ovd-info', 'ovd-info', 'manual', 'running', "
                "now() - interval '5 hours', now() - interval '5 hours')"
            )
        )
    app.dependency_overrides[get_readiness_checker] = lambda: _checker(
        session_factory, qdrant_configured=False
    )

    body = client.get("/health/ready").json()

    assert body["status"] == "degraded"
    assert body["components"]["monitoring"]["status"] == "degraded"


def test_valid_request_id_is_echoed(client: TestClient) -> None:
    response = client.get("/health/live", headers={"X-Request-ID": "trace-42.a_b"})

    assert response.headers["X-Request-ID"] == "trace-42.a_b"


@pytest.mark.parametrize("header", ["x" * 65, "has space", "<script>", ""])
def test_untrusted_request_id_is_replaced(client: TestClient, header: str) -> None:
    response = client.get("/health/live", headers={"X-Request-ID": header})

    replaced = response.headers["X-Request-ID"]
    assert replaced != header
    assert len(replaced) == 32
    int(replaced, 16)


def test_unhandled_error_returns_error_model_without_traceback(client: TestClient) -> None:
    def broken() -> ResearchService:
        raise RuntimeError("boom at /home/app/secret.py line 12")

    app.dependency_overrides[get_research_service] = broken

    response = client.post(
        "/research", json={"object_type": "person"}, headers={"X-Request-ID": "req-1"}
    )

    assert response.status_code == 500
    assert response.json() == {
        "error": {
            "code": "internal_error",
            "message": "Internal server error",
            "request_id": "req-1",
        }
    }
    assert "Traceback" not in response.text
    assert "secret.py" not in response.text
    assert response.headers["X-Request-ID"] == "req-1"


def test_openapi_lists_main_endpoints(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]

    for path in (
        "/health/live",
        "/health/ready",
        "/research",
        "/research/query",
        "/candidates",
        "/monitoring/runs",
        "/monitoring/findings",
        "/person-resolution/reviews",
    ):
        assert path in paths
