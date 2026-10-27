"""«Работа»: the existing pipeline as one operator work cycle, read through `/api/v1/cycle`."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker
from support.db_fixtures import DatabaseSeeder
from support.pipeline_runs import finish_steps

from orchestrator.operations import OperationParameters, OperationRegistry
from processing.storage.models import EntityGroupRecord, EntityGroupRoleRecord, JunkScreenHoldRecord
from web.app import app
from web.dependencies import OPERATION_COMMAND_LINE, get_db, get_operation_registry


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


def _cycle(client: TestClient) -> dict[str, Any]:
    cycle: dict[str, Any] = client.get("/api/v1/cycle").json()
    return cycle


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
    registry = OperationRegistry(
        session_factory, command_line=OPERATION_COMMAND_LINE, executor=lambda _work: None
    )

    with _client(session_factory, registry) as client:
        cycle = _cycle(client)

    # Nothing to review; one press runs every step that is left, from step 1.
    assert cycle["attention"] is None and cycle["tasks"] == []
    assert cycle["current_stage"] == "load" and cycle["latest_run_id"] == 0
    assert cycle["chain_question"] == "Выполнить шаги 1–5 подряд?"
    assert cycle["chain_span"] == "шаги 1–5 подряд"
    assert [step["status"] for step in cycle["steps"]] == [
        "ready",
        "waiting",
        "waiting",
        "waiting",
        "waiting",
    ]


def test_cycle_menu_count_includes_junk_held_for_a_decision(
    session_factory: sessionmaker[Session],
) -> None:
    _held(session_factory)

    registry = OperationRegistry(
        session_factory, command_line=OPERATION_COMMAND_LINE, executor=lambda _work: None
    )
    with _client(session_factory, registry) as client:
        cycle = _cycle(client)
        page = client.get("/ui/logs").text

    # The menu counts it on every page.
    assert '<span>Работа</span><span class="nav-count">1</span>' in page
    attention = cycle["attention"]
    assert (attention["key"], attention["count"]) == ("junk_holds", 1)
    assert attention["title"].startswith("Возможно мусор")
    assert attention["href"] == "/ui/junk-holds"


def test_cycle_step_uses_the_existing_action_and_returns_to_the_cycle(
    session_factory: sessionmaker[Session],
) -> None:
    registry = OperationRegistry(
        session_factory, command_line=OPERATION_COMMAND_LINE, executor=lambda _work: None
    )

    with _client(session_factory, registry) as client:
        started = client.post("/ui/management/run?back=cycle", follow_redirects=False)
        cycle = _cycle(client)

    assert (started.status_code, started.headers["location"]) == (303, "/ui/cycle")
    assert cycle["steps"][0]["status"] == "running" and cycle["live"] is not None


def test_every_page_names_the_same_live_next_station(
    session_factory: sessionmaker[Session],
) -> None:
    registry = OperationRegistry(
        session_factory, command_line=OPERATION_COMMAND_LINE, executor=lambda _work: None
    )
    finish_steps(session_factory, registry, "load")

    with _client(session_factory, registry) as client:
        logs = client.get("/ui/logs").text
        runs = client.get("/ui/runs").text

    next_action = "Шаг 2: очистить от мусора."
    assert f"<strong>Дальше:</strong> {next_action}" in logs
    assert f"<strong>Дальше:</strong> {next_action}" in runs


def test_cycle_makes_the_highest_priority_review_the_only_primary_action(
    session_factory: sessionmaker[Session],
) -> None:
    _held(session_factory)
    registry = OperationRegistry(
        session_factory, command_line=OPERATION_COMMAND_LINE, executor=lambda _work: None
    )
    finish_steps(session_factory, registry, "load", "purge")

    with _client(session_factory, registry) as client:
        cycle = _cycle(client)

    # The one thing to do first is the review of the highest priority.
    assert cycle["attention"]["key"] == "junk_holds"
    assert "Проверьте, относятся ли удержанные публикации" in cycle["attention"]["description"]
    assert "junk_holds" not in [task["key"] for task in cycle["tasks"]]
    assert cycle["current_stage"] == "entities"


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
    registry = OperationRegistry(
        session_factory, command_line=OPERATION_COMMAND_LINE, executor=lambda _work: None
    )
    finish_steps(session_factory, registry, "load", "purge", "entities", "figurants")

    with _client(session_factory, registry) as client:
        cycle = _cycle(client)

    attention = cycle["attention"]
    assert attention["key"] == "roles" and attention["title"].startswith("Неясные роли")
    assert attention["href"] == "/ui/roles"
    assert cycle["current_stage"] == "political"


def test_cycle_describes_an_active_automatic_run_without_an_operator_cta(
    session_factory: sessionmaker[Session],
) -> None:
    registry = OperationRegistry(
        session_factory, command_line=OPERATION_COMMAND_LINE, executor=lambda _work: None
    )
    registry.start("monitor", OperationParameters(mode="load", sources=["ovd-info"]))

    with _client(session_factory, registry) as client:
        cycle = _cycle(client)

    assert cycle["steps"][0]["status"] == "running" and cycle["live"] is not None
    assert cycle["attention"] is None


def test_common_next_action_does_not_wait_for_a_stale_run(
    session_factory: sessionmaker[Session],
) -> None:
    registry = OperationRegistry(
        session_factory, command_line=OPERATION_COMMAND_LINE, executor=lambda _work: None
    )
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
        page = client.get("/ui/logs").text

    assert "<strong>Дальше:</strong> Шаг 1: подгрузить статьи." in page
    assert "Дождитесь завершения" not in page


def test_do_all_starts_a_chain_from_the_step_that_is_due(
    session_factory: sessionmaker[Session],
) -> None:
    registry = OperationRegistry(
        session_factory, command_line=OPERATION_COMMAND_LINE, executor=lambda _work: None
    )
    done = finish_steps(session_factory, registry, "load", "purge", "entities", "figurants")

    with _client(session_factory, registry) as client:
        last = _cycle(client)
        address = f"/ui/management/political?chain=1&after={last['latest_run_id']}&back=cycle"
        started = client.post(address, follow_redirects=False)
        running = _cycle(client)

    # The last step alone is left: «Сделать всё» says so.
    # The press names the latest run the answer was read for.
    assert last["latest_run_id"] == done[-1]
    assert last["chain_question"] == "Выполнить шаг 5?" and last["chain_span"] == "шаг 5"
    assert (started.status_code, started.headers["location"]) == (303, "/ui/cycle")
    run = registry.runs_of("monitor", limit=1)[0]
    assert (run.parameters.mode, run.parameters.chain) == ("political", True)
    assert running["chain_note"] == "Идёт «Сделать всё»: это последний шаг."
    assert running["live"] is not None


def test_a_live_chained_step_says_what_starts_after_it(
    session_factory: sessionmaker[Session],
) -> None:
    registry = OperationRegistry(
        session_factory, command_line=OPERATION_COMMAND_LINE, executor=lambda _work: None
    )
    finish_steps(session_factory, registry, "load")
    registry.start("monitor", OperationParameters(mode="purge", chain=True))

    with _client(session_factory, registry) as client:
        cycle = _cycle(client)
        runs = client.get("/ui/runs").text

    note = (
        "Идёт «Сделать всё»: после этого шага сам запустится «Собрать сущности». "
        "«Остановить» прерывает и цепочку."
    )
    assert cycle["chain_note"] == note and note in runs


def test_a_live_single_step_says_nothing_of_a_chain(session_factory: sessionmaker[Session]) -> None:
    registry = OperationRegistry(
        session_factory, command_line=OPERATION_COMMAND_LINE, executor=lambda _work: None
    )
    registry.start("monitor", OperationParameters(mode="load", sources=["ovd-info"]))

    with _client(session_factory, registry) as client:
        assert _cycle(client)["chain_note"] == ""


def test_a_chain_that_stopped_says_where_and_why(session_factory: sessionmaker[Session]) -> None:
    registry = OperationRegistry(
        session_factory, command_line=OPERATION_COMMAND_LINE, executor=lambda _work: None
    )

    def chained(mode: str, status: str) -> int:
        run = registry.start(
            "monitor",
            OperationParameters(
                mode=mode,  # type: ignore[arg-type]
                chain=True,
                sources=["ovd-info"] if mode == "load" else None,
            ),
        )
        with session_factory.begin() as session:
            session.execute(
                text(
                    "UPDATE operator_operation_runs SET status = :status, started_at = now(), "
                    "finished_at = now() WHERE id = :id"
                ),
                {"status": status, "id": run.id},
            )
        return run.id

    with _client(session_factory, registry) as client:
        nothing = _cycle(client)["chain_stopped"]
        first = chained("load", "succeeded")
        lost = _cycle(client)["chain_stopped"]
        crashed = chained("purge", "failed")
        failed, runs = _cycle(client)["chain_stopped"], client.get("/ui/runs").text
        chained("purge", "interrupted")
        stopped = _cycle(client)["chain_stopped"]
        finish_steps(session_factory, registry, "purge")
        by_hand = _cycle(client)["chain_stopped"]

    assert nothing == ""
    # The step ended well and the next never started: the chain was lost, not the step.
    assert (
        f"«Сделать всё» остановилось на шаге 1 (запуск #{first}): следующий шаг не запустился "
        "(сервер перезапускался или шаг запустили вручную). Кнопка продолжит с шага 2."
    ) in lost
    assert (
        f"«Сделать всё» остановилось на шаге 2 (запуск #{crashed}): шаг завершился с ошибкой"
    ) in failed and "Кнопка продолжит с шага 2." in failed
    assert "шаг был остановлен или потерян" in stopped
    # The runs' page says it above its own button, and the card names the chain.
    assert "«Сделать всё» остановилось на шаге 2" in runs
    assert "· шаг цепочки «Сделать всё» ·" in runs
    # A step pressed by hand afterwards is no chain: nothing to say.
    assert by_hand == ""


def test_a_chain_that_reached_the_last_step_says_nothing(
    session_factory: sessionmaker[Session],
) -> None:
    registry = OperationRegistry(
        session_factory, command_line=OPERATION_COMMAND_LINE, executor=lambda _work: None
    )
    finish_steps(session_factory, registry, "load", "purge", "entities", "figurants")
    run = registry.start("monitor", OperationParameters(mode="political", chain=True))
    with session_factory.begin() as session:
        session.execute(
            text("UPDATE operator_operation_runs SET status = 'succeeded' WHERE id = :id"),
            {"id": run.id},
        )

    with _client(session_factory, registry) as client:
        assert _cycle(client)["chain_stopped"] == ""


def test_the_same_press_cannot_start_a_second_chain(session_factory: sessionmaker[Session]) -> None:
    """The form sent again from the browser's history after the cycle went round: the
    first step is due again, and the old press would delete and spend with nobody asked."""
    registry = OperationRegistry(
        session_factory, command_line=OPERATION_COMMAND_LINE, executor=lambda _work: None
    )

    with _client(session_factory, registry) as client:
        first = client.post("/ui/management/run?chain=1&after=0&back=cycle", follow_redirects=False)
        with session_factory.begin() as session:
            session.execute(text("UPDATE operator_operation_runs SET status = 'succeeded'"))
        finish_steps(session_factory, registry, "purge", "entities", "figurants", "political")
        again = client.post("/ui/management/run?chain=1&after=0&back=cycle", follow_redirects=False)
        unnamed = client.post("/ui/management/run?chain=1&back=cycle", follow_redirects=False)
        fresh = _cycle(client)["latest_run_id"]
        pressed = client.post(
            f"/ui/management/run?chain=1&after={fresh}&back=cycle", follow_redirects=False
        )
        # A single step is not a chain: it needs no such word.
        single = client.post("/ui/management/purge", follow_redirects=False)

    assert first.status_code == 303
    assert again.status_code == 409 and "Это нажатие «Сделать всё» устарело" in again.text
    assert unnamed.status_code == 409
    assert pressed.status_code == 303
    assert single.status_code == 409 and "устарело" not in single.text
    assert len(registry.runs_of("monitor", limit=20)) == 6


def test_an_old_press_is_refused_on_every_step_and_stop_reaches_the_chain(
    session_factory: sessionmaker[Session],
) -> None:
    registry = OperationRegistry(
        session_factory, command_line=OPERATION_COMMAND_LINE, executor=lambda _work: None
    )
    (loaded,) = finish_steps(session_factory, registry, "load")

    with _client(session_factory, registry) as client:
        old = client.post("/ui/management/purge?chain=1&after=0", follow_redirects=False)
        purge = client.post(f"/ui/management/purge?chain=1&after={loaded}", follow_redirects=False)
        ended = registry.runs_of("monitor", limit=1)[0].id
        # The purge ends and its chain starts the next step — and only then «Остановить»,
        # pressed on the page that still showed the purge, arrives.
        with session_factory.begin() as session:
            session.execute(
                text("UPDATE operator_operation_runs SET status = 'succeeded' WHERE id = :id"),
                {"id": ended},
            )
        following = registry.start("monitor", OperationParameters(mode="entities", chain=True))
        stopped = client.post(
            f"/ui/management/runs/{ended}/stop", data={"back": "cycle"}, follow_redirects=False
        )

    assert old.status_code == 409 and "устарело" in old.text
    assert purge.status_code == 303
    assert stopped.status_code == 303
    assert registry.get(following.id).status.value == "interrupted"
    assert registry.get(ended).status.value == "succeeded"
