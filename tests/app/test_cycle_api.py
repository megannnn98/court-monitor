"""`/api/v1/cycle`: «Работа» for the React console — the queues, the steps, «Сделать всё»
and «Остановить» — through the legacy page's own functions. The executor never runs a
step: who may start, and when, are the registry's own answers."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from operator_console import OperationRegistry, OperationRunStatus
from web.app import app
from web.dependencies import get_db, get_operation_registry


@contextmanager
def _client(
    session_factory: sessionmaker[Session], registry: OperationRegistry
) -> Iterator[TestClient]:
    def override() -> Iterator[Session]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override
    app.dependency_overrides[get_operation_registry] = lambda: registry
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_operation_registry, None)


def test_a_fresh_base_waits_for_step_one(session_factory: sessionmaker[Session]) -> None:
    registry = OperationRegistry(session_factory, executor=lambda _work: None)

    with _client(session_factory, registry) as client:
        cycle = client.get("/api/v1/cycle").json()
        page = client.get("/ui/cycle").text

    assert cycle["attention"] is None and cycle["tasks"] == []
    assert cycle["current_stage"] == "load" and cycle["live"] is None
    assert cycle["latest_run_id"] == 0
    assert [(step["stage"], step["status"]) for step in cycle["steps"]] == [
        ("load", "ready"),
        ("purge", "waiting"),
        ("entities", "waiting"),
        ("figurants", "waiting"),
        ("political", "waiting"),
    ]
    for step in cycle["steps"]:
        assert step["label"] in page
    assert cycle["chain_span"] == "шаги 1–5 подряд"
    # Nothing is paid here: the question only asks to start.
    assert cycle["chain_question"] == "Выполнить шаги 1–5 подряд?"


def test_do_all_starts_once_and_a_stale_press_is_refused(
    session_factory: sessionmaker[Session],
) -> None:
    registry = OperationRegistry(session_factory, executor=lambda _work: None)

    with _client(session_factory, registry) as client:
        started = client.post("/api/v1/cycle/start", json={"after": 0})
        live = client.get("/api/v1/cycle").json()
        busy = client.post("/api/v1/cycle/start", json={"after": started.json()["run_id"]})
        with session_factory.begin() as session:
            session.execute(text("UPDATE operator_operation_runs SET status = 'succeeded'"))
        stale = client.post("/api/v1/cycle/start", json={"after": 0})
        foreign = client.post(
            "/api/v1/cycle/start",
            json={"after": started.json()["run_id"]},
            headers={"Origin": "https://evil.example"},
        )

    assert started.status_code == 200 and started.json()["stage"] == "load"
    run_id = started.json()["run_id"]
    assert live["live"] == {"run_id": run_id, "title": "Подгрузить статьи"}
    assert live["steps"][0]["status"] == "running"
    assert "Идёт «Сделать всё»" in live["chain_note"]
    assert busy.status_code == 409 and busy.json()["detail"].startswith(f"Идёт запуск #{run_id}")
    assert stale.status_code == 409 and "устарело" in stale.json()["detail"]
    # Another site cannot spend the operator's money.
    assert foreign.status_code == 403
    assert len(registry.runs_of("monitor", limit=10)) == 1


def test_stop_ends_a_live_run_and_says_when_it_had_ended(
    session_factory: sessionmaker[Session],
) -> None:
    registry = OperationRegistry(session_factory, executor=lambda _work: None)

    with _client(session_factory, registry) as client:
        run_id = client.post("/api/v1/cycle/start", json={"after": 0}).json()["run_id"]
        stopped = client.post(f"/api/v1/cycle/runs/{run_id}/stop")
        again = client.post(f"/api/v1/cycle/runs/{run_id}/stop")
        missing = client.post("/api/v1/cycle/runs/999999/stop")

    assert stopped.json() == {"run_id": run_id, "stopped": True}
    assert again.json() == {"run_id": run_id, "stopped": False}
    assert missing.status_code == 404
    assert registry.get(run_id).status is OperationRunStatus.INTERRUPTED
