"""`GET /api/v1/status`: the legacy status strip and menu counters for the React console.

Read through the same functions as the strip, so the numbers cannot drift apart."""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from monitor_core.errors import ParseError
from orchestrator.models import MonitoringStage, MonitoringTrigger
from orchestrator.repository import SqlAlchemyMonitoringRepository
from orchestrator.storage.models import MonitoringRunRecord, OperatorOperationRunRecord
from web.app import app
from web.dependencies import get_db


@contextmanager
def _client(session_factory: sessionmaker[Session]) -> Iterator[TestClient]:
    def override() -> Iterator[Session]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)


def _strip(page: str, label: str) -> str:
    found = re.search(rf"<small>{label}</small><strong>([^<]*)</strong>", page)
    assert found, label
    return found.group(1)


def test_an_empty_database_says_nothing_runs(session_factory: sessionmaker[Session]) -> None:
    with _client(session_factory) as client:
        status = client.get("/api/v1/status")

    assert status.status_code == 200
    body = status.json()
    assert (body["articles"], body["people"], body["result"]) == (0, 0, 0)
    assert body["queue"]["total"] == 0
    assert body["latest_monitoring_status"] is None
    assert body["live_operation"] is None
    assert body["next_action"]


def test_the_route_says_what_the_strip_says(session_factory: sessionmaker[Session]) -> None:
    now = datetime.now(UTC)
    with session_factory.begin() as session:
        session.add(
            MonitoringRunRecord(
                scope="source:ovd",
                source="ovd",
                trigger_type="manual",
                status="completed_with_errors",
                parameters={},
                started_at=now,
                heartbeat_at=now,
            )
        )
        session.add(
            OperatorOperationRunRecord(
                operation_name="monitor",
                parameters={"mode": "entities"},
                command=["court-monitor"],
                status="running",
                started_at=now,
                heartbeat_at=now,
            )
        )

    with _client(session_factory) as client:
        status = client.get("/api/v1/status").json()
        page = client.get("/ui/about").text

    assert str(status["articles"]) == _strip(page, "Публикации")
    assert str(status["people"]) == _strip(page, "Люди")
    assert str(status["result"]) == _strip(page, "Результат")
    assert str(status["queue"]["total"]) == _strip(page, "Очередь")
    assert status["latest_monitoring_status"] == "completed_with_errors"
    assert _strip(page, "Последний запуск") == "завершён с ошибками"
    assert status["live_operation"] == {"mode": "entities", "title": "Сборка сущностей"}
    assert "Идёт: Сборка сущностей" in page


def test_status_lists_a_source_that_looks_like_its_layout_changed(
    session_factory: sessionmaker[Session],
) -> None:
    monitoring = SqlAlchemyMonitoringRepository(session_factory)
    run_id = monitoring.start_run(
        scope="source:kommersant",
        source="kommersant",
        trigger=MonitoringTrigger.MANUAL,
        parameters={},
        stale_after=timedelta(hours=1),
    )
    monitoring.add_counters(run_id, {"documents_discovered": 3, "documents_failed": 3})
    for _ in range(3):
        monitoring.record_failure(
            run_id,
            stage=MonitoringStage.INGESTION,
            entity_type="source_document",
            error=ParseError("Article text not found"),
        )
    monitoring.finish_run(run_id)

    with _client(session_factory) as client:
        status = client.get("/api/v1/status").json()

    assert status["layout_suspects"] == [
        {
            "source": "kommersant",
            "signal": "parse",
            "run_id": run_id,
            "since": status["layout_suspects"][0]["since"],
            "count": 3,
            "total": 3,
            "text": f"kommersant: в запуске №{run_id} не разобраны 3 из 3 статей — похоже, сайт сменил вёрстку статьи.",
        }
    ]
