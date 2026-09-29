"""The home page presents the existing pipeline as one operator work cycle."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker
from support.db_fixtures import DatabaseSeeder
from support.pipeline_runs import finish_steps

from db.orm_models import EntityGroupRecord, EntityGroupRoleRecord, JunkScreenHoldRecord
from operator_console import OperationParameters, OperationRegistry
from web.app import app
from web.dependencies import get_db, get_operation_registry


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


def _held(session_factory: sessionmaker[Session]) -> None:
    with session_factory() as session:
        seed = DatabaseSeeder(session)
        source = seed.source("cycle-test", "https://example.test")
        article_id, _run = seed.article(
            source,
            external_id="cycle",
            title="Cycle",
            text="Text",
            published_at=datetime(2026, 9, 28, tzinfo=UTC),
        )
        session.add(
            JunkScreenHoldRecord(
                article_id=article_id,
                status="held",
                score=0.8,
                cutoff=0.7,
                screen="test",
                reason="check",
            )
        )
        session.commit()


def test_cycle_is_a_task_centric_dashboard_with_secondary_processing_status(
    session_factory: sessionmaker[Session],
) -> None:
    registry = OperationRegistry(session_factory, executor=lambda _work: None)

    with _client(session_factory, registry) as client:
        response = client.get("/ui/cycle")

    assert response.status_code == 200
    assert "<h1>Работа</h1>" in response.text
    assert "Сейчас ничего проверять не нужно" in response.text
    assert "Обработка данных" in response.text
    assert 'id="step-load"' in response.text
    assert 'formaction="/ui/management/run?back=cycle"' in response.text
    assert 'id="step-purge"' not in response.text
    assert 'href="/ui/political"><svg' in response.text
    assert "<span>Работа</span>" in response.text
    assert "<span>Результаты</span>" in response.text
    assert "<span>Поиск</span>" in response.text
    assert "source-table" not in response.text
    assert 'id="source-errors"' not in response.text


def test_cycle_menu_count_includes_junk_held_for_a_decision(
    session_factory: sessionmaker[Session],
) -> None:
    _held(session_factory)

    registry = OperationRegistry(session_factory, executor=lambda _work: None)
    with _client(session_factory, registry) as client:
        page = client.get("/ui/cycle").text

    assert '<span>Работа</span><span class="nav-count">1</span>' in page
    assert 'data-primary-task="junk_holds"' in page
    assert "Публикации на проверке" in page
    assert 'href="/ui/junk-holds"' in page
    assert ">Начать проверку</a>" in page


def test_cycle_step_uses_the_existing_action_and_returns_to_the_cycle(
    session_factory: sessionmaker[Session],
) -> None:
    registry = OperationRegistry(session_factory, executor=lambda _work: None)

    with _client(session_factory, registry) as client:
        started = client.post("/ui/management/run?back=cycle", follow_redirects=False)
        page = client.get("/ui/cycle")

    assert (started.status_code, started.headers["location"]) == (303, "/ui/cycle")
    assert 'class="pipeline-current running"' in page.text
    assert 'name="back" value="cycle"' in page.text


def test_every_page_names_the_same_live_next_station(
    session_factory: sessionmaker[Session],
) -> None:
    registry = OperationRegistry(session_factory, executor=lambda _work: None)
    finish_steps(session_factory, registry, "load")

    with _client(session_factory, registry) as client:
        cycle = client.get("/ui/cycle").text
        overview = client.get("/ui/overview").text

    next_action = "Шаг 2: очистить от мусора."
    assert f"<strong>Дальше:</strong> {next_action}" in cycle
    assert f"<strong>Дальше:</strong> {next_action}" in overview


def test_cycle_makes_the_highest_priority_review_the_only_primary_action(
    session_factory: sessionmaker[Session],
) -> None:
    _held(session_factory)
    registry = OperationRegistry(session_factory, executor=lambda _work: None)
    finish_steps(session_factory, registry, "load", "purge")

    with _client(session_factory, registry) as client:
        page = client.get("/ui/cycle").text

    assert 'data-primary-task="junk_holds"' in page
    assert page.count('class="primary-action"') == 1
    assert "Проверьте, относятся ли удержанные публикации" in page
    assert 'id="step-entities"' in page


def test_cycle_shows_unclear_roles_after_higher_priority_queues_are_empty(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        group = EntityGroupRecord(
            key="cycle-person",
            name="Иван Иванов",
            variants=[],
            mention_count=1,
            article_count=1,
            event_types={},
        )
        session.add(group)
        session.flush()
        session.add(
            EntityGroupRoleRecord(
                group_id=group.id,
                role="unclear",
                kind=None,
                method="model",
                reason="Недостаточно данных.",
                quote="",
            )
        )
        session.commit()
    registry = OperationRegistry(session_factory, executor=lambda _work: None)
    finish_steps(session_factory, registry, "load", "purge", "entities", "figurants")

    with _client(session_factory, registry) as client:
        page = client.get("/ui/cycle").text

    assert 'data-primary-task="roles"' in page
    assert "Неясные роли" in page
    assert 'href="/ui/roles"' in page
    assert 'id="step-political"' in page


def test_cycle_describes_an_active_automatic_run_without_an_operator_cta(
    session_factory: sessionmaker[Session],
) -> None:
    registry = OperationRegistry(session_factory, executor=lambda _work: None)
    registry.start("monitor", OperationParameters(mode="load", sources=["ovd-info"]))

    with _client(session_factory, registry) as client:
        page = client.get("/ui/cycle").text

    assert 'class="pipeline-current running"' in page
    assert "Идёт автоматическая обработка" in page
    assert 'class="primary-action"' not in page


def test_common_next_action_does_not_wait_for_a_stale_run(
    session_factory: sessionmaker[Session],
) -> None:
    registry = OperationRegistry(session_factory, executor=lambda _work: None)
    run = registry.start("monitor", OperationParameters(mode="load", sources=["ovd-info"]))
    with session_factory.begin() as session:
        session.execute(
            text(
                "UPDATE operator_operation_runs SET created_at = now() - interval '10 minutes' "
                "WHERE id = :id"
            ),
            {"id": run.id},
        )

    with _client(session_factory, registry) as client:
        overview = client.get("/ui/overview").text

    assert "<strong>Дальше:</strong> Шаг 1: подгрузить статьи." in overview
    assert "Дождитесь завершения" not in overview
