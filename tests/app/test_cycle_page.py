"""The home page presents the existing pipeline as one operator work cycle."""

from __future__ import annotations

import re
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


def test_cycle_shows_steps_and_review_stations_in_work_order(
    session_factory: sessionmaker[Session],
) -> None:
    registry = OperationRegistry(session_factory, executor=lambda _work: None)

    with _client(session_factory, registry) as client:
        response = client.get("/ui/cycle")

    assert response.status_code == 200
    expected = [
        "1. Подгрузить",
        "Сбои извлечения",
        "2. Очистить",
        "Отсев",
        "3. Сущности",
        "Пары",
        "4. Фигуранты",
        "Роль",
        "5. Политические дела",
        "Политичность",
        "Безымянные",
        "Результат",
    ]
    cycle = response.text[response.text.index('<form method="post" class="cycle">') :]
    assert [heading.strip() for heading in re.findall(r"<h2>([^<]+)", cycle)] == expected
    assert 'id="step-load"' in response.text
    assert 'formaction="/ui/management/run?back=cycle"' in response.text
    assert 'id="step-purge"' not in response.text
    assert 'href="/ui/junk-holds"' in response.text
    assert 'href="/ui/pairs"' in response.text
    assert 'href="/ui/unnamed"' in response.text
    assert "source-table" not in response.text
    assert 'id="source-errors"' not in response.text


def test_cycle_menu_count_includes_junk_held_for_a_decision(
    session_factory: sessionmaker[Session],
) -> None:
    _held(session_factory)

    registry = OperationRegistry(session_factory, executor=lambda _work: None)
    with _client(session_factory, registry) as client:
        page = client.get("/ui/cycle").text

    assert '<span>Рабочий цикл</span><span class="nav-count">1</span>' in page
    assert 'href="/ui/junk-holds"' in page and "Отсев" in page


def test_cycle_step_uses_the_existing_action_and_returns_to_the_cycle(
    session_factory: sessionmaker[Session],
) -> None:
    registry = OperationRegistry(session_factory, executor=lambda _work: None)

    with _client(session_factory, registry) as client:
        started = client.post("/ui/management/run?back=cycle", follow_redirects=False)
        page = client.get("/ui/cycle")

    assert (started.status_code, started.headers["location"]) == (303, "/ui/cycle")
    assert 'class="cycle-station step-station running"' in page.text
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


def test_unfinished_previous_review_warns_and_requires_confirmation(
    session_factory: sessionmaker[Session],
) -> None:
    _held(session_factory)
    registry = OperationRegistry(session_factory, executor=lambda _work: None)
    finish_steps(session_factory, registry, "load", "purge")

    with _client(session_factory, registry) as client:
        page = client.get("/ui/cycle").text

    assert "Проверить отсев: 1." in page
    assert 'class="cycle-station review-station warning"' in page
    assert ">Разобрать (1)</a>" in page
    assert "Предыдущая проверка «Отсев» не завершена: 1" in page
    assert 'id="step-entities"' in page


def test_unclear_role_warns_but_does_not_replace_the_next_pipeline_step(
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

    assert "<strong>Дальше:</strong> Шаг 5: отобрать политические дела." in page
    assert ">Проверить (1)</a>" in page
    assert "Предыдущая проверка «Роль» не завершена: 1" in page
    assert 'id="step-political"' in page


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
