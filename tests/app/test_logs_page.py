"""The logs page shows run output as it comes, a live run shows its progress and can be
stopped."""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from monitoring.models import MonitoringTrigger
from monitoring.repository import SqlAlchemyMonitoringRepository
from operator_console import (
    OperationParameters,
    OperationRegistry,
    OperationRunStatus,
    ProcessResult,
)
from web.app import app
from web.dependencies import get_db, get_operation_registry

SELECTION = OperationParameters(sources=["ovd-info"])


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


def test_the_logs_page_shows_the_latest_run_output_escaped(
    session_factory: sessionmaker[Session],
) -> None:
    registry = OperationRegistry(
        session_factory,
        executor=lambda work: work(),
        process_runner=lambda command, heartbeat: ProcessResult(
            1, '[{"source": "ovd-info"}]', "ERROR <b>boom</b>\n"
        ),
    )
    run = registry.start("monitor", SELECTION)

    with _client(session_factory, registry) as client:
        page = client.get("/ui/logs")

    assert page.status_code == 200
    assert 'href="/ui/logs">Логи</a>' in page.text
    assert f"Запуск #{run.id}" in page.text
    assert "ERROR &lt;b&gt;boom&lt;/b&gt;" in page.text
    assert "[{&quot;source&quot;: &quot;ovd-info&quot;}]" in page.text
    # An ended run has nothing to stop and nothing to refresh.
    assert "/stop" not in page.text
    assert "window.location.reload" not in page.text


def test_a_live_run_is_refreshed_and_can_be_stopped_from_the_logs(
    session_factory: sessionmaker[Session],
) -> None:
    registry = OperationRegistry(session_factory, executor=lambda _work: None)
    run = registry.start("monitor", SELECTION)

    with _client(session_factory, registry) as client:
        page = client.get(f"/ui/logs?run_id={run.id}")
        management = client.get(f"/ui/management?run_id={run.id}")
        stopped = client.post(
            f"/ui/management/runs/{run.id}/stop", data={"back": "logs"}, follow_redirects=False
        )
        after = client.get(stopped.headers["location"])

    assert f'action="/ui/management/runs/{run.id}/stop"' in page.text
    assert "window.location.reload" in page.text
    assert f'action="/ui/management/runs/{run.id}/stop"' in management.text
    assert stopped.status_code == 303
    assert stopped.headers["location"] == f"/ui/logs?run_id={run.id}"
    assert registry.get(run.id).status is OperationRunStatus.INTERRUPTED
    assert "stopped by the operator" in after.text
    assert "/stop" not in after.text


def test_stop_returns_to_management_by_default_and_unknown_runs_are_404(
    session_factory: sessionmaker[Session],
) -> None:
    registry = OperationRegistry(session_factory, executor=lambda _work: None)
    run = registry.start("monitor", SELECTION)

    with _client(session_factory, registry) as client:
        stopped = client.post(
            f"/ui/management/runs/{run.id}/stop",
            data={"back": "https://evil.example"},
            follow_redirects=False,
        )
        unknown = client.post("/ui/management/runs/999999/stop", follow_redirects=False)
        unknown_log = client.get("/ui/logs?run_id=999999")

    assert stopped.headers["location"] == f"/ui/management?run_id={run.id}"
    assert (unknown.status_code, unknown_log.status_code) == (404, 404)


def _running_run(session_factory: sessionmaker[Session], registry: OperationRegistry) -> int:
    run = registry.start(
        "monitor", OperationParameters(sources=["ovd-info", "sota-vision", "tg-mash"])
    )
    # Claimed by a worker a minute ago; monitoring runs of it start after that.
    with session_factory.begin() as session:
        session.execute(
            text(
                "UPDATE operator_operation_runs SET status = 'running', "
                "started_at = now() - interval '1 minute', heartbeat_at = now() WHERE id = :id"
            ),
            {"id": run.id},
        )
    return run.id


def _monitoring_run(repository: SqlAlchemyMonitoringRepository, source: str | None) -> int:
    return repository.start_run(
        scope=f"source:{source}" if source else "derived",
        source=source,
        trigger=MonitoringTrigger.MANUAL,
        parameters={},
        stale_after=timedelta(hours=1),
    )


def _progress_box(page: str) -> str:
    match = re.search(r'<div class="progress-box">.*?</div>', page, re.DOTALL)
    assert match is not None
    return match.group(0)


def test_a_live_run_shows_which_source_of_how_many_and_how_far_into_it(
    session_factory: sessionmaker[Session],
) -> None:
    registry = OperationRegistry(session_factory, executor=lambda _work: None)
    run_id = _running_run(session_factory, registry)
    monitoring = SqlAlchemyMonitoringRepository(session_factory)
    monitoring.finish_run(_monitoring_run(monitoring, "ovd-info"))
    current = _monitoring_run(monitoring, "sota-vision")
    monitoring.add_counters(
        current, {"documents_discovered": 10, "documents_skipped": 2, "documents_ingested": 3}
    )

    with _client(session_factory, registry) as client:
        management = _progress_box(client.get(f"/ui/management?run_id={run_id}").text)
        logs = _progress_box(client.get(f"/ui/logs?run_id={run_id}").text)

    assert management == logs
    assert '<progress class="overall" value="1" max="4">' in management
    assert "Источник 2 из 3: SOTA" in management
    assert '<progress class="step" value="5" max="10">' in management
    assert "Документов 5 из 10: загружено 3, уже были 2, ошибок 0" in management


def test_the_last_step_is_the_shared_classification(
    session_factory: sessionmaker[Session],
) -> None:
    registry = OperationRegistry(session_factory, executor=lambda _work: None)
    run_id = _running_run(session_factory, registry)
    monitoring = SqlAlchemyMonitoringRepository(session_factory)
    for source in ("ovd-info", "sota-vision", "tg-mash"):
        monitoring.finish_run(_monitoring_run(monitoring, source))
    _monitoring_run(monitoring, None)

    with _client(session_factory, registry) as client:
        box = _progress_box(client.get(f"/ui/management?run_id={run_id}").text)

    assert '<progress class="overall" value="3" max="4">' in box
    assert "Шаг 4 из 4: общая классификация и сверка с РФМ" in box


def test_a_pending_run_is_getting_ready_and_an_ended_one_has_no_progress(
    session_factory: sessionmaker[Session],
) -> None:
    registry = OperationRegistry(session_factory, executor=lambda _work: None)
    run = registry.start("monitor", SELECTION)

    with _client(session_factory, registry) as client:
        pending = client.get(f"/ui/management?run_id={run.id}").text
        registry.stop(run.id)
        ended = client.get(f"/ui/management?run_id={run.id}").text

    assert "Запуск готовится" in _progress_box(pending)
    assert "progress-box" not in ended
