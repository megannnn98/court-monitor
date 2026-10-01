"""Every page that shows a running step shows its pulse and last lines: a page that says
«выполняется» and nothing else cannot be told from a hung one."""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker
from support.pipeline_runs import finish_steps

from operator_console import OperationParameters, OperationRegistry
from web.app import app
from web.dependencies import get_db, get_operation_registry
from web.ui.run_tail import pulse

LOG = (
    "2026-10-01 09:44:10,771 INFO monitoring request_id=- event=entities_rf_check_stage "
    "stage=downloading\n"
    "2026-10-01 09:44:12,202 INFO entities request_id=- event=rf_list_downloaded bytes=4305049\n"
    '2026-10-01 09:44:13,623 INFO httpx request_id=- HTTP Request: GET https://x.test "200"\n'
    "2026-10-01 09:44:14,001 INFO entities request_id=- event=entity_politics_stage "
    "stage=asking 50/3200\n"
)
TAIL = [
    "event=entities_rf_check_stage stage=downloading",
    "event=rf_list_downloaded bytes=4305049",
    "event=entity_politics_stage stage=asking 50/3200",
]


@contextmanager
def _client(
    session_factory: sessionmaker[Session], registry: OperationRegistry
) -> Iterator[TestClient]:
    def override_get_db() -> Iterator[Session]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_operation_registry] = lambda: registry
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_operation_registry, None)


def _live(
    session_factory: sessionmaker[Session],
    registry: OperationRegistry,
    mode: str,
    *,
    log: str = LOG,
    signal: str = "20 seconds",
    status: str = "running",
) -> int:
    """A run of `mode`, with the last sign of life `signal` ago."""
    run = registry.start(
        "monitor",
        OperationParameters(
            mode=mode,  # type: ignore[arg-type]
            sources=["ovd-info"] if mode == "load" else None,
        ),
    )
    started = "NULL" if status == "pending" else "now() - interval '10 minutes'"
    with session_factory.begin() as session:
        session.execute(
            text(
                f"UPDATE operator_operation_runs SET status = :status, started_at = {started}, "
                "heartbeat_at = now() - CAST(:signal AS interval), stderr = :log WHERE id = :id"
            ),
            {"id": run.id, "status": status, "signal": signal, "log": log},
        )
    return run.id


def _tail(page: str) -> list[str]:
    found = re.search(r'<pre class="log-tail"[^>]*>(.*?)</pre>', page, re.DOTALL)
    return found.group(1).splitlines() if found else []


def _registry(session_factory: sessionmaker[Session], *done: str) -> OperationRegistry:
    registry = OperationRegistry(session_factory, executor=lambda _work: None)
    if done:
        finish_steps(session_factory, registry, *done)
    return registry


def test_the_page_of_the_cycle_shows_the_pulse_and_the_last_lines_of_the_running_step(
    session_factory: sessionmaker[Session],
) -> None:
    """The page the operator stays on: it showed a step «выполняется» and nothing else."""
    registry = _registry(session_factory, "load")
    _live(session_factory, registry, "purge")

    with _client(session_factory, registry) as client:
        page = client.get("/ui/cycle").text

    assert _tail(page) == TAIL
    seen = re.search(r"Процесс отвечает: последний сигнал (\d+) с назад", page)
    assert seen is not None and 20 <= int(seen.group(1)) < 40


def test_a_silent_process_is_told_from_a_lost_one(
    session_factory: sessionmaker[Session],
) -> None:
    """A step that waits for a model says nothing for a minute; a lost process says nothing
    for good. The line says how long, and after five minutes says it is probably lost."""
    registry = _registry(session_factory, "load")
    _live(session_factory, registry, "purge", signal="3 minutes")
    with _client(session_factory, registry) as client:
        quiet = client.get("/ui/cycle").text
    assert "последний сигнал 3 мин назад" in quiet and "Сигнала от процесса нет" not in quiet

    # Past five minutes the runner marks the run lost, and the pages stop showing it as live;
    # the words are for the moment between the last sign and that verdict.
    with session_factory.begin() as session:
        session.execute(text("TRUNCATE operator_operation_runs RESTART IDENTITY CASCADE"))
    registry = _registry(session_factory, "load")
    run_id = _live(session_factory, registry, "purge", signal="6 minutes")
    lost = pulse(registry.get(run_id))
    assert 'class="warning log-pulse">Сигнала от процесса нет уже 6 мин' in lost
    with _client(session_factory, registry) as client:
        assert "Процесс отвечает" not in client.get("/ui/cycle").text


def test_a_running_step_that_has_said_nothing_yet_says_so(
    session_factory: sessionmaker[Session],
) -> None:
    """No lines, or only requests to a site: not an empty box, a word."""
    registry = _registry(session_factory, "load")
    _live(
        session_factory,
        registry,
        "purge",
        log='2026-10-01 09:44:13,623 INFO httpx request_id=- HTTP Request: GET https://x.test "200"\n',
    )

    with _client(session_factory, registry) as client:
        page = client.get("/ui/cycle").text

    assert "Процесс пока ничего не написал в журнал." in page and _tail(page) == []


def test_a_run_that_has_not_started_says_it_is_getting_ready(
    session_factory: sessionmaker[Session],
) -> None:
    registry = _registry(session_factory, "load")
    _live(session_factory, registry, "purge", log="", status="pending")

    with _client(session_factory, registry) as client:
        page = client.get("/ui/cycle").text

    assert "Запуск готовится…" in page and "Процесс отвечает" not in page


@pytest.mark.parametrize(
    ("mode", "done"),
    [
        ("load", ()),
        ("purge", ("load",)),
        ("entities", ("load", "purge")),
        ("figurants", ("load", "purge", "entities")),
        ("political", ("load", "purge", "entities", "figurants")),
    ],
)
def test_every_step_shows_its_pulse_and_lines_on_the_cycle_and_the_management_pages(
    session_factory: sessionmaker[Session], mode: str, done: tuple[str, ...]
) -> None:
    registry = _registry(session_factory, *done)
    run_id = _live(session_factory, registry, mode)

    with _client(session_factory, registry) as client:
        cycle = client.get("/ui/cycle").text
        management = client.get(f"/ui/runs?run_id={run_id}").text

    for page in (cycle, management):
        assert _tail(page) == TAIL, mode
        assert "Процесс отвечает: последний сигнал" in page, mode


def test_the_entities_page_shows_them_while_the_rebuild_runs(
    session_factory: sessionmaker[Session],
) -> None:
    registry = _registry(session_factory, "load", "purge")
    _live(session_factory, registry, "entities")

    with _client(session_factory, registry) as client:
        page = client.get("/ui/entities").text

    assert _tail(page) == TAIL and "Процесс отвечает" in page


def test_an_ended_run_has_no_pulse_and_a_bad_end_keeps_its_lines(
    session_factory: sessionmaker[Session],
) -> None:
    registry = _registry(session_factory, "load")
    run_id = _live(session_factory, registry, "purge")

    with _client(session_factory, registry) as client:
        pages = {}
        for status in ("failed", "interrupted", "succeeded"):
            with session_factory.begin() as session:
                session.execute(
                    text("UPDATE operator_operation_runs SET status = :s WHERE id = :id"),
                    {"s": status, "id": run_id},
                )
            pages[status] = client.get(f"/ui/runs?run_id={run_id}").text

    for status in ("failed", "interrupted"):
        assert _tail(pages[status]) == TAIL and "Процесс отвечает" not in pages[status]
    assert _tail(pages["succeeded"]) == [] and "Процесс отвечает" not in pages["succeeded"]
