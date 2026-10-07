"""The read-only v1 API duplicates legacy endpoint contracts exactly."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker
from support.db_fixtures import DatabaseSeeder

from api import app, get_db
from db.orm_models import MonitoringRunRecord
from operator_console import OperationParameters, OperationRegistry


@pytest.fixture
def db_client(session_factory: sessionmaker[Session]) -> Iterator[TestClient]:
    def override_get_db() -> Iterator[Session]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)


def _responses_match(client: TestClient, path: str) -> None:
    legacy = client.get(path)
    versioned = client.get(f"/api/v1{path}")

    assert versioned.status_code == legacy.status_code
    assert legacy.json()
    assert versioned.json() == legacy.json()


def test_versioned_read_endpoints_match_legacy_contracts(
    db_client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    with session_factory.begin() as session:
        seed = DatabaseSeeder(session)
        person_id = seed.person("Иван Иванов")
        source_id = seed.source("ОВД-Инфо", "https://ovd.example")
        article_id, _ = seed.article(
            source_id,
            external_id="article-1",
            title="Заголовок",
            text="Иван Иванов",
        )
        seed.classification(person_id, "political", 0.9)
        snapshot_id = seed.snapshot()
        seed.entry(snapshot_id, "Петров Пётр")
        seed.match(person_id, snapshot_id, "not_matched", 0.9)
        session.add(
            MonitoringRunRecord(
                scope="source:ovd-info",
                source="ovd-info",
                trigger_type="manual",
                status="completed",
                parameters={},
                started_at=datetime.now(UTC),
                heartbeat_at=datetime.now(UTC),
            )
        )

    OperationRegistry(session_factory, executor=lambda work: None).start(
        "discover-and-ingest", OperationParameters(source="ovd-info", limit=1)
    )

    for path in (
        "/persons",
        f"/articles/{article_id}",
        f"/candidates?snapshot_id={snapshot_id}",
        "/rosfinmonitoring/snapshots",
        "/monitoring/runs",
        "/operations/runs",
        "/health/live",
    ):
        _responses_match(db_client, path)


def test_versioned_not_found_error_matches_legacy_contract(db_client: TestClient) -> None:
    _responses_match(db_client, "/persons/999999")
