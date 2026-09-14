"""Read-only monitoring endpoints over PostgreSQL."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker
from support.monitoring_fixtures import (
    MALFORMED_MARKER,
    PETROV,
    SIDOROV,
    FakeUpstream,
    build_service,
    import_rf_snapshot,
)

from api import app, get_db


@pytest.fixture
def client(session_factory: sessionmaker[Session]) -> Iterator[TestClient]:
    def override_get_db() -> Iterator[Session]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_status_runs_details_and_findings(
    session_factory: sessionmaker[Session], client: TestClient
) -> None:
    import_rf_snapshot(session_factory, [("Петр Петров", "03.03.1970")])
    upstream = FakeUpstream()
    upstream.publish("sidorov", SIDOROV)
    upstream.publish("broken", f"{MALFORMED_MARKER} {PETROV}")
    run = build_service(session_factory, {"ovd-info": upstream}).run_source("ovd-info")

    status = client.get("/monitoring/status")
    runs = client.get("/monitoring/runs", params={"source": "ovd-info"})
    details = client.get(f"/monitoring/runs/{run.id}")
    findings = client.get("/monitoring/findings")
    filtered = client.get("/monitoring/runs", params={"status": "completed"})

    assert status.status_code == 200
    assert status.json()["running"] == []
    assert status.json()["active_findings"] == 1
    assert [state["source_name"] for state in status.json()["sources"]] == ["ovd-info"]
    assert runs.status_code == 200
    assert [(item["id"], item["status"]) for item in runs.json()] == [
        (run.id, "completed_with_errors")
    ]
    assert details.status_code == 200
    assert details.json()["run"]["documents_ingested"] == 2
    assert [(item["stage"], item["failure_kind"]) for item in details.json()["items"]] == [
        ("extraction", "non_retryable")
    ]
    assert findings.status_code == 200
    assert [
        (item["finding_type"], item["first_seen_run_id"], item["active"])
        for item in findings.json()
    ] == [("political_persecution_not_in_rf", run.id, True)]
    assert filtered.json() == []


def test_unknown_run_is_404(client: TestClient) -> None:
    assert client.get("/monitoring/runs/999").status_code == 404


def test_invalid_status_filter_is_422(client: TestClient) -> None:
    assert client.get("/monitoring/runs", params={"status": "exploded"}).status_code == 422


def test_monitoring_api_has_no_write_endpoints() -> None:
    methods = {
        method
        for route in app.routes
        if getattr(route, "path", "").startswith("/monitoring")
        for method in getattr(route, "methods", set())
    }
    assert methods <= {"GET", "HEAD"}
