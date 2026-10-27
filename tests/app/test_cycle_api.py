"""`/api/v1/cycle`: «Работа» for the React console — the queues, the steps, «Сделать всё»
and «Остановить» — through the legacy page's own functions. The executor never runs a
step: who may start, and when, are the registry's own answers."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from orchestrator.operations import OperationRegistry, OperationRunStatus
from web.app import app
from web.dependencies import OPERATION_COMMAND_LINE, get_db, get_operation_registry


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
    registry = OperationRegistry(
        session_factory, command_line=OPERATION_COMMAND_LINE, executor=lambda _work: None
    )

    with _client(session_factory, registry) as client:
        cycle = client.get("/api/v1/cycle").json()

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
    assert all(step["label"] for step in cycle["steps"])
    assert cycle["chain_span"] == "шаги 1–5 подряд"
    # Nothing is paid here: the question only asks to start.
    assert cycle["chain_question"] == "Выполнить шаги 1–5 подряд?"


def test_do_all_starts_once_and_a_stale_press_is_refused(
    session_factory: sessionmaker[Session],
) -> None:
    registry = OperationRegistry(
        session_factory, command_line=OPERATION_COMMAND_LINE, executor=lambda _work: None
    )

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
    registry = OperationRegistry(
        session_factory, command_line=OPERATION_COMMAND_LINE, executor=lambda _work: None
    )

    with _client(session_factory, registry) as client:
        run_id = client.post("/api/v1/cycle/start", json={"after": 0}).json()["run_id"]
        stopped = client.post(f"/api/v1/cycle/runs/{run_id}/stop")
        again = client.post(f"/api/v1/cycle/runs/{run_id}/stop")
        missing = client.post("/api/v1/cycle/runs/999999/stop")

    assert stopped.json() == {"run_id": run_id, "stopped": True}
    assert again.json() == {"run_id": run_id, "stopped": False}
    assert missing.status_code == 404
    assert registry.get(run_id).status is OperationRunStatus.INTERRUPTED


def _finish_all(session_factory: sessionmaker[Session]) -> None:
    with session_factory.begin() as session:
        session.execute(
            text("UPDATE operator_operation_runs SET status = 'succeeded', finished_at = now()")
        )


def test_the_page_tells_the_working_date_and_the_last_load(
    session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PIPELINE_SINCE", "2026-09-20")
    registry = OperationRegistry(
        session_factory, command_line=OPERATION_COMMAND_LINE, executor=lambda _work: None
    )

    with _client(session_factory, registry) as client:
        fresh = client.get("/api/v1/cycle").json()
        client.post(
            "/api/v1/cycle/start",
            json={"after": 0, "published_from": "2026-10-01", "published_to": "2026-10-10"},
        )
        _finish_all(session_factory)
        loaded = client.get("/api/v1/cycle").json()

    assert fresh["working_date"] == "2026-09-20"
    # A press with dates is the round from step 1, whatever step it is at.
    assert fresh["period_question"] == "Выполнить шаги 1–5 подряд?"
    assert fresh["last_load"] is None
    assert loaded["last_load"]["published_from"] == "2026-10-01"
    assert loaded["last_load"]["published_to"] == "2026-10-10"
    assert loaded["last_load"]["finished_at"] is not None


def test_a_period_press_loads_that_period_even_after_step_one(
    session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    """With dates, «Сделать всё» is steps 1–5 for them: the load the operator asked for,
    whatever step the round is at."""
    monkeypatch.setenv("PIPELINE_SINCE", "2026-09-20")
    registry = OperationRegistry(
        session_factory, command_line=OPERATION_COMMAND_LINE, executor=lambda _work: None
    )

    with _client(session_factory, registry) as client:
        first = client.post("/api/v1/cycle/start", json={"after": 0}).json()["run_id"]
        _finish_all(session_factory)
        assert client.get("/api/v1/cycle").json()["current_stage"] == "purge"
        started = client.post(
            "/api/v1/cycle/start", json={"after": first, "published_from": "2026-10-01"}
        )

    assert started.status_code == 200, started.text
    assert started.json()["stage"] == "load"
    run = registry.get(started.json()["run_id"])
    assert run.parameters.mode == "load" and run.parameters.chain
    assert (run.parameters.published_from, run.parameters.published_to) == ("2026-10-01", None)
    assert run.command[run.command.index("--published-from") + 1] == "2026-10-01"
    assert "--published-to" not in run.command


@pytest.mark.parametrize(
    ("period", "refusal"),
    [
        ({"published_from": "2026-09-01"}, "раньше рабочей даты 20.09.2026"),
        ({"published_from": "2026-10-10", "published_to": "2026-10-01"}, "позже даты «по»"),
        (
            {
                "published_from": "2026-10-01",
                "published_to": (datetime.now(UTC).date() + timedelta(days=2)).isoformat(),
            },
            "в будущем",
        ),
        ({"published_to": "2026-10-01"}, "нужна дата «с»"),
    ],
)
def test_a_period_the_round_cannot_keep_is_refused(
    session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
    period: dict[str, str],
    refusal: str,
) -> None:
    monkeypatch.setenv("PIPELINE_SINCE", "2026-09-20")
    registry = OperationRegistry(
        session_factory, command_line=OPERATION_COMMAND_LINE, executor=lambda _work: None
    )

    with _client(session_factory, registry) as client:
        refused = client.post("/api/v1/cycle/start", json={"after": 0, **period})

    assert refused.status_code == 400
    assert refusal in refused.json()["detail"]
    assert registry.runs_of("monitor", limit=10) == []
