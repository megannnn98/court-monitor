"""Liveness/readiness, request ids and the API error model."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from api import app, get_db, get_readiness_checker
from health import ReadinessChecker, expected_schema_revision


@pytest.fixture
def client() -> Iterator[TestClient]:
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


def _checker(
    session_factory: sessionmaker[Session] | None,
    *,
    revision: str | None = None,
) -> ReadinessChecker:
    return ReadinessChecker(
        session_factory,
        expected_revision=revision or expected_schema_revision(),
        stale_run_after=timedelta(minutes=120),
    )


def test_liveness_needs_no_dependencies(client: TestClient) -> None:
    response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "alive"}


def test_versioned_liveness_matches_legacy_path(client: TestClient) -> None:
    legacy = client.get("/health/live")
    versioned = client.get("/api/v1/health/live")

    assert versioned.status_code == legacy.status_code == 200
    assert versioned.json() == legacy.json()


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
    assert body["components"]["monitoring"]["status"] == "ok"


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
    app.dependency_overrides[get_readiness_checker] = lambda: _checker(None)

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
    app.dependency_overrides[get_readiness_checker] = lambda: _checker(session_factory)

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
    def broken() -> Session:
        raise RuntimeError("boom at /home/app/secret.py line 12")

    app.dependency_overrides[get_db] = broken

    response = client.get("/persons", headers={"X-Request-ID": "req-1"})

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
        "/candidates",
        "/monitoring/runs",
        "/monitoring/findings",
        "/person-resolution/reviews",
    ):
        assert path in paths


def test_openapi_lists_versioned_reads_and_only_the_known_actions(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]

    for path in (
        "/api/v1/health/live",
        "/api/v1/candidates",
        "/api/v1/persons",
        "/api/v1/articles/{article_id}",
        "/api/v1/operations/runs",
    ):
        assert path in paths

    # Reads, and exactly the console's actions (refused from other origins, ADR 0022):
    # a mutation added by accident fails here.
    actions = {
        (method, path)
        for path, item in paths.items()
        if path.startswith("/api/v1/")
        for method in item
        if method != "get"
    }
    assert actions == {
        ("post", "/api/v1/political/done"),
        ("post", "/api/v1/review/roles/decide"),
        ("post", "/api/v1/review/politics/decide"),
        ("post", "/api/v1/review/pairs/decide"),
        ("post", "/api/v1/cycle/start"),
        ("post", "/api/v1/cycle/runs/{run_id}/stop"),
        ("post", "/api/v1/unnamed/keep"),
        ("post", "/api/v1/unnamed/reject"),
        ("post", "/api/v1/unnamed/resolve"),
        ("post", "/api/v1/unnamed/clear"),
        ("post", "/api/v1/base-unnamed/decide"),
    }


def test_openapi_keeps_legacy_operation_ids_and_has_no_duplicates(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]
    operation_ids = [
        operation["operationId"]
        for path in paths.values()
        for operation in path.values()
        if isinstance(operation, dict) and "operationId" in operation
    ]

    assert paths["/persons"]["get"]["operationId"] == "list_persons_persons_get"
    assert len(operation_ids) == len(set(operation_ids))
