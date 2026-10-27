"""`GET /api/v1/about`: the data of «О системе» for the React console.

It must say what the legacy page says, because both read it through one function: the
build stamp, the totals, the last successful run."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from orchestrator.storage.models import OperatorOperationRunRecord
from web.app import app
from web.build_info import build_info
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


def _succeeded_run(session_factory: sessionmaker[Session], started: datetime) -> None:
    with session_factory.begin() as session:
        session.add(
            OperatorOperationRunRecord(
                operation_name="pipeline",
                parameters={},
                command=["court-monitor"],
                status="succeeded",
                started_at=started,
            )
        )


def test_the_route_answers_with_the_build_and_the_totals(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        articles = session.scalar(text("SELECT count(*) FROM parsed_articles"))
        people = session.scalar(text("SELECT count(*) FROM entity_groups"))

    with _client(session_factory) as client:
        response = client.get("/api/v1/about")

    assert response.status_code == 200
    about = response.json()
    info = build_info()
    assert (about["version"], about["tag"], about["commit"], about["built_at"]) == (
        info.version,
        info.tag,
        info.commit,
        info.built_at,
    )
    assert (about["articles"], about["people"]) == (articles, people)
    assert about["checked_at"]


def test_the_route_names_the_last_successful_run(session_factory: sessionmaker[Session]) -> None:
    with session_factory.begin() as session:
        session.execute(text("DELETE FROM operator_operation_runs"))
    _succeeded_run(session_factory, datetime(2026, 10, 1, 9, 30, tzinfo=UTC))

    with _client(session_factory) as client:
        about = client.get("/api/v1/about").json()

    started = datetime.fromisoformat(about["last_successful_run_at"])
    assert started == datetime(2026, 10, 1, 9, 30, tzinfo=UTC)


def test_no_successful_run_is_said_as_none(session_factory: sessionmaker[Session]) -> None:
    with session_factory.begin() as session:
        session.execute(text("DELETE FROM operator_operation_runs"))

    with _client(session_factory) as client:
        about = client.get("/api/v1/about").json()

    assert about["last_successful_run_at"] is None
