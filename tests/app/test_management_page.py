"""Manual source selection starts one recorded monitoring operation."""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker
from support.pipeline_runs import finish_steps
from support.research_db_fixtures import ResearchSeeder

from db.orm_models import OperatorOperationRunRecord
from monitoring.models import MonitoringStage, MonitoringTrigger
from monitoring.repository import SqlAlchemyMonitoringRepository
from operator_console import OperationParameters, OperationRegistry, OperationRunStatus
from sources.source_registry import news_sources
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


def test_the_home_page_has_no_source_list_step_one_loads_them_all(
    session_factory: sessionmaker[Session],
) -> None:
    registry = OperationRegistry(session_factory, executor=lambda _work: None)

    with _client(session_factory, registry) as client:
        response = client.get("/ui/management")

    assert response.status_code == 200
    # «Управление» is under «Система» now; «Обзор» is the home page.
    assert '<a class="active" href="/ui/management" aria-current="page"><svg class="icon"' in (
        response.text
    )
    assert 'name="sources"' not in response.text and "source-table" not in response.text
    assert 'name="published_from"' in response.text
    assert 'name="published_to"' in response.text
    assert f"Шаг 1 скачивает все новостные источники ({len(news_sources())})" in response.text
    assert "Пять шагов по кругу" in response.text
    assert "Четыре шага по кругу" not in response.text


def test_the_console_status_strip_is_in_russian(
    session_factory: sessionmaker[Session],
) -> None:
    registry = OperationRegistry(session_factory, executor=lambda _work: None)

    with _client(session_factory, registry) as client:
        page = client.get("/ui/management").text

    assert "<small>Публикации</small>" in page
    assert "<small>Люди</small>" in page
    assert "<small>Очередь</small>" in page
    assert "<small>Последний запуск</small>" in page
    assert "Persons:" not in page
    assert "ER pending:" not in page
    assert "Последний run:" not in page


def test_post_starts_one_tracked_run_for_the_selected_sources(
    session_factory: sessionmaker[Session],
) -> None:
    queued: list[object] = []
    registry = OperationRegistry(session_factory, executor=queued.append)

    with _client(session_factory, registry) as client:
        response = client.post(
            "/ui/management/run",
            data={"sources": ["sota-vision", "ovd-info", "sota-vision"]},
            follow_redirects=False,
        )
        run_page = client.get(response.headers["location"])

    assert response.status_code == 303
    run_id = int(response.headers["location"].rsplit("=", 1)[1])
    run = registry.get(run_id)
    assert run.status is OperationRunStatus.PENDING
    assert run.parameters.sources == ["sota-vision", "ovd-info"]
    assert run.parameters.mode == "load"
    assert run.command[-7:] == [
        "--selected-source",
        "sota-vision",
        "--selected-source",
        "ovd-info",
        "--load-only",
        "--limit",
        "50",
    ]
    assert len(queued) == 1
    assert "Запуск #" in run_page.text
    assert "Ждут очереди: 2" in run_page.text
    with session_factory() as session:
        assert session.get(OperatorOperationRunRecord, run_id) is not None


def test_step_one_without_sources_loads_every_news_source(
    session_factory: sessionmaker[Session],
) -> None:
    registry = OperationRegistry(session_factory, executor=lambda _work: None)

    with _client(session_factory, registry) as client:
        everything = client.post("/ui/management/run", data={}, follow_redirects=False)

    assert everything.status_code == 303
    (run,) = registry.runs_of("monitor")
    assert run.parameters.sources == [source.name for source in news_sources()]
    assert "memopzk-figurants" not in run.parameters.sources


def test_post_passes_published_date_range_to_monitor(
    session_factory: sessionmaker[Session],
) -> None:
    registry = OperationRegistry(session_factory, executor=lambda _work: None)

    with _client(session_factory, registry) as client:
        response = client.post(
            "/ui/management/run",
            data={"published_from": "2026-09-01", "published_to": "2026-09-25"},
            follow_redirects=False,
        )
        run_page = client.get(response.headers["location"])

    assert response.status_code == 303
    assert "Период публикаций: " in run_page.text
    assert "2026-09-01 — 2026-09-25" in run_page.text
    run = registry.runs_of("monitor")[0]
    assert run.parameters.published_from == "2026-09-01"
    assert run.parameters.published_to == "2026-09-25"
    assert "--published-from" in run.command
    assert "--published-to" in run.command


def test_post_rejects_reversed_published_date_range(
    session_factory: sessionmaker[Session],
) -> None:
    registry = OperationRegistry(session_factory, executor=lambda _work: None)

    with _client(session_factory, registry) as client:
        response = client.post(
            "/ui/management/run",
            data={"published_from": "2026-09-25", "published_to": "2026-09-01"},
        )

    assert response.status_code == 400
    assert "не позже" in response.text
    assert registry.runs_of("monitor") == []


def test_a_registry_source_does_not_start_a_run(session_factory: sessionmaker[Session]) -> None:
    registry = OperationRegistry(session_factory, executor=lambda _work: None)

    with _client(session_factory, registry) as client:
        registry_source = client.post("/ui/management/run", data={"sources": ["memopzk-figurants"]})

    assert registry_source.status_code == 400
    assert "не новостной источник" in registry_source.text
    assert registry.runs_of("monitor") == []


def test_the_first_step_counts_every_source(
    session_factory: sessionmaker[Session],
) -> None:
    registry = OperationRegistry(session_factory, executor=lambda _work: None)

    with _client(session_factory, registry) as client:
        full = _run_bar(client.get("/ui/management").text)

    total = len(news_sources())
    assert (
        'id="step-load" class="step current run-button" type="submit" '
        'formaction="/ui/management/run"'
    ) in full
    assert f'1. Подгрузить статьи (<span class="selected-count">{total}</span>)' in full
    assert not re.search(r'id="step-load"[^>]* disabled>', full)


def test_the_latest_manual_runs_are_listed_with_their_status(
    session_factory: sessionmaker[Session],
) -> None:
    registry = OperationRegistry(session_factory, executor=lambda _work: None)

    with _client(session_factory, registry) as client:
        client.post("/ui/management/run", data={"sources": ["ovd-info"]})
        page = client.get("/ui/management").text

    run_id = registry.runs_of("monitor")[0].id
    assert "Последние ручные запуски" in page
    assert f'<tr class="current"><td><a href="/ui/management?run_id={run_id}">#{run_id}</a>' in page
    assert '<span class="badge pending">В очереди</span>' in page


def test_the_stylesheet_url_carries_its_version(session_factory: sessionmaker[Session]) -> None:
    registry = OperationRegistry(session_factory, executor=lambda _work: None)

    with _client(session_factory, registry) as client:
        page = client.get("/ui/management").text

    assert re.search(r'href="/static/local-ui\.css\?v=[0-9a-f]{12}"', page)


def test_person_resolution_has_no_button_and_no_route(
    session_factory: sessionmaker[Session],
) -> None:
    registry = OperationRegistry(session_factory, executor=lambda _work: None)
    finish_steps(session_factory, registry, "load", "purge", "entities")

    with _client(session_factory, registry) as client:
        bar = _run_bar(client.get("/ui/management").text)
        response = client.post("/ui/management/resolve", data={"sources": ["ovd-info"]})

    assert "Разрешить персоны" not in bar
    # After entities come the figurants; the list is checked in the final step.
    assert re.findall(r'id="step-(\w+)" class="step current', bar) == ["figurants"]
    assert response.status_code == 404
    assert registry.runs_of("monitor")[0].parameters.mode == "entities"


def test_an_earlier_resolution_run_still_shows_its_card(
    session_factory: sessionmaker[Session],
) -> None:
    registry = OperationRegistry(session_factory, executor=lambda _work: None)
    run = registry.start(
        "monitor", OperationParameters(sources=["sota-vision", "ovd-info"], mode="resolve")
    )

    with _client(session_factory, registry) as client:
        run_page = client.get(f"/ui/management?run_id={run.id}")

    assert run.command[2:] == [
        "monitor-resolve",
        "--selected-source",
        "sota-vision",
        "--selected-source",
        "ovd-info",
    ]
    assert "Разрешение персон" in run_page.text
    assert '">Новых людей</th>' in run_page.text
    assert "<b>На проверку</b> — Спорные упоминания" in run_page.text
    assert "Общая классификация и сверка с РФМ" in run_page.text


def test_a_step_out_of_turn_is_refused_by_the_server(
    session_factory: sessionmaker[Session],
) -> None:
    registry = OperationRegistry(session_factory, executor=lambda _work: None)

    with _client(session_factory, registry) as client:
        early = client.post("/ui/management/entities")
        early_purge = client.post("/ui/management/purge")
        client.post("/ui/management/run", data={"sources": ["ovd-info"]}, follow_redirects=False)
        while_loading = client.post("/ui/management/purge")

    assert early.status_code == 409 and "Сейчас шаг 1: «Подгрузить статьи»" in early.text
    assert early_purge.status_code == 409
    assert while_loading.status_code == 409 and "Идёт запуск #" in while_loading.text
    assert [run.parameters.mode for run in registry.runs_of("monitor")] == ["load"]


def _live(session_factory: sessionmaker[Session], registry: OperationRegistry, mode: str) -> int:
    run = registry.start(
        "monitor",
        OperationParameters(sources=["ovd-info", "sota-vision"], mode=mode),  # type: ignore[arg-type]
    )
    with session_factory.begin() as session:
        session.execute(
            text(
                "UPDATE operator_operation_runs SET status = 'running', "
                "started_at = now() - interval '1 minute', heartbeat_at = now() WHERE id = :id"
            ),
            {"id": run.id},
        )
    return run.id


def test_a_live_resolution_shows_how_many_articles_of_the_source_are_done(
    session_factory: sessionmaker[Session],
) -> None:
    registry = OperationRegistry(session_factory, executor=lambda _work: None)
    run_id = _live(session_factory, registry, "resolve")
    monitoring = SqlAlchemyMonitoringRepository(session_factory)
    current = monitoring.start_run(
        scope="source:ovd-info",
        source="ovd-info",
        trigger=MonitoringTrigger.MANUAL,
        parameters={},
        stale_after=timedelta(hours=1),
    )
    monitoring.set_stage_metrics(
        current, MonitoringStage.RESOLUTION, {"extraction_runs": 10, "done": 3}
    )

    with _client(session_factory, registry) as client:
        page = client.get(f"/ui/management?run_id={run_id}").text

    assert '<progress class="overall" value="0" max="3">' in page
    assert "Источник 1 из 2: ОВД-Инфо" in page
    assert '<progress class="step" value="3" max="10">' in page
    assert "Разрешение персон: статей 3 из 10" in page


def test_a_load_has_no_classification_step(session_factory: sessionmaker[Session]) -> None:
    registry = OperationRegistry(session_factory, executor=lambda _work: None)
    run_id = _live(session_factory, registry, "load")

    with _client(session_factory, registry) as client:
        page = client.get(f"/ui/management?run_id={run_id}").text

    assert '<progress class="overall" value="0" max="2">' in page
    assert "Загрузка статей" in page
    assert "Общая классификация и сверка с РФМ" not in page


def _details(page: str) -> str:
    match = re.search(r"<details( open)?>.*?</details>", page, re.DOTALL)
    assert match is not None
    return match.group(0)


def test_an_ended_run_is_a_card_that_counts_the_sources_it_never_reached(
    session_factory: sessionmaker[Session],
) -> None:
    registry = OperationRegistry(session_factory, executor=lambda _work: None)
    sources = ["ovd-info", "sota-vision", "tg-mash", "tg-astrapress"]
    run = registry.start("monitor", OperationParameters(sources=sources, mode="load"))
    with session_factory.begin() as session:
        session.execute(
            text(
                "UPDATE operator_operation_runs SET status = 'running', "
                "started_at = now() - interval '1 minute', heartbeat_at = now() WHERE id = :id"
            ),
            {"id": run.id},
        )
    monitoring = SqlAlchemyMonitoringRepository(session_factory)

    def started(source: str) -> int:
        return monitoring.start_run(
            scope=f"source:{source}",
            source=source,
            trigger=MonitoringTrigger.MANUAL,
            parameters={},
            stale_after=timedelta(hours=1),
        )

    monitoring.finish_run(started("ovd-info"))
    started("sota-vision")  # its process died with the run: left `running`
    registry.stop(run.id)

    with _client(session_factory, registry) as client:
        page = client.get(f"/ui/management?run_id={run.id}").text

    assert "Готово: 1" in page
    assert "Прервано: 1" in page
    assert "Не запускались: 2" in page
    details = _details(page)
    assert details.startswith("<details>")  # folded once the run ended
    assert "Подробно по источникам (2)" in details
    assert "ovd-info" in details and "sota-vision" in details
    assert "tg-mash" not in details and "tg-astrapress" not in details


def test_a_source_held_by_another_run_is_busy_even_when_the_report_was_cut(
    session_factory: sessionmaker[Session],
) -> None:
    """The CLI's JSON report is cut to its tail; the busy source is read from the runs."""
    registry = OperationRegistry(session_factory, executor=lambda _work: None)
    monitoring = SqlAlchemyMonitoringRepository(session_factory)
    held = monitoring.start_run(
        scope="source:tg-mash",
        source="tg-mash",
        trigger=MonitoringTrigger.MANUAL,
        parameters={},
        stale_after=timedelta(hours=3),
    )
    run = registry.start("monitor", OperationParameters(sources=["tg-mash", "ovd-info"]))
    with session_factory.begin() as session:
        session.execute(
            text(
                "UPDATE monitoring_runs SET started_at = now() - interval '2 hours' WHERE id = :id"
            ),
            {"id": held},
        )
        session.execute(
            text(
                "UPDATE operator_operation_runs SET status = 'failed', stdout = '[{\"source\": \"tr', "
                "started_at = now() - interval '1 minute', finished_at = now() WHERE id = :id"
            ),
            {"id": run.id},
        )

    with _client(session_factory, registry) as client:
        page = client.get(f"/ui/management?run_id={run.id}").text

    assert "Заняты другим запуском: 1" in page
    assert "Не запускались: 1" in page
    assert "tg-mash" in _details(page)


def test_every_number_column_of_a_load_says_what_it_counts(
    session_factory: sessionmaker[Session],
) -> None:
    registry = OperationRegistry(session_factory, executor=lambda _work: None)
    run = registry.start("monitor", OperationParameters(sources=["ovd-info"], mode="load"))
    with session_factory.begin() as session:
        session.execute(
            text(
                "UPDATE operator_operation_runs SET status = 'running', "
                "started_at = now() - interval '1 minute', heartbeat_at = now() WHERE id = :id"
            ),
            {"id": run.id},
        )
    monitoring = SqlAlchemyMonitoringRepository(session_factory)
    loaded = monitoring.start_run(
        scope="source:ovd-info",
        source="ovd-info",
        trigger=MonitoringTrigger.MANUAL,
        parameters={},
        stale_after=timedelta(hours=1),
    )
    monitoring.add_counters(
        loaded,
        {
            "documents_discovered": 50,
            "documents_skipped": 47,
            "documents_ingested": 3,
            "articles_extracted": 5,
            "events_created": 2,
        },
    )
    monitoring.finish_run(loaded)

    with _client(session_factory, registry) as client:
        details = _details(client.get(f"/ui/management?run_id={run.id}").text)

    headers = re.findall(r'<th title="([^"]+)">([^<]+)</th>', details)
    assert [header for _, header in headers] == [
        "Просмотрено",
        "Уже были",
        "Новых",
        "Разобрано",
        "Событий",
        "Ошибок",
        "Сообщение",
    ]
    assert all(meaning for meaning, _ in headers)
    row = re.search(r"<tr><td>ОВД-Инфо.*?</tr>", details, re.DOTALL)
    assert row is not None
    assert re.findall(r'<td class="num">(\d+)</td>', row.group(0)) == [
        "50",
        "47",
        "3",
        "5",
        "2",
        "0",
    ]
    assert "<b>Уже были</b> — Из просмотренных: уже в базе" in details


def _run_bar(page: str) -> str:
    match = re.search(r'<div class="run-bar">.*?</div>', page, re.DOTALL)
    assert match is not None
    return match.group(0)


def test_while_a_load_runs_its_button_stops_it(session_factory: sessionmaker[Session]) -> None:
    registry = OperationRegistry(session_factory, executor=lambda _work: None)

    with _client(session_factory, registry) as client:
        client.post("/ui/management/run", data={"sources": ["ovd-info"]}, follow_redirects=False)
        run_id = registry.runs_of("monitor")[0].id
        # An old run on screen: the bar still belongs to the run going on now.
        busy = _run_bar(client.get("/ui/management?run_id=" + str(run_id)).text)
        stopped = client.post(
            f"/ui/management/runs/{run_id}/stop",
            data={"sources": ["ovd-info"], "back": "management"},
            follow_redirects=False,
        )
        after = _run_bar(client.get(stopped.headers["location"]).text)

    assert f'formaction="/ui/management/runs/{run_id}/stop"' in busy
    assert "■ Остановить: Подгрузить статьи" in busy
    assert 'id="step-' not in busy  # nothing else can be pressed
    assert stopped.headers["location"] == f"/ui/management?run_id={run_id}"
    assert registry.get(run_id).status is OperationRunStatus.INTERRUPTED
    # A stopped load is repeated.
    assert 'id="step-load" class="step current' in after


def test_while_a_resolution_runs_its_button_stops_it(
    session_factory: sessionmaker[Session],
) -> None:
    registry = OperationRegistry(session_factory, executor=lambda _work: None)
    finish_steps(session_factory, registry, "load", "purge", "entities")

    # A resolution has no button, but one started before (or by the bot) can be stopped.
    run_id = _live(session_factory, registry, "resolve")

    with _client(session_factory, registry) as client:
        page = client.get("/ui/management").text
        bar = _run_bar(page)
        refused = client.post("/ui/management/run", data={"sources": ["ovd-info"]})

    steps = re.findall(r"<button [^>]*>([^<]*)", bar)
    assert [step.split(" (")[0] for step in steps] == [
        "■ Остановить: Разрешение персон",
        "2. Очистить от мусора",
        "3. Собрать сущности",
        "4. Найти фигурантов",
        "5. Отобрать политические дела и сверить с РФМ",
    ]
    assert f'formaction="/ui/management/runs/{run_id}/stop"' in bar
    assert "Идёт разрешение персон." in page
    assert refused.status_code == 409 and "(Разрешение персон)" in refused.text


def test_the_purge_runs_in_the_background_and_its_button_stops_it(
    session_factory: sessionmaker[Session],
) -> None:
    registry = OperationRegistry(session_factory, executor=lambda _work: None)
    finish_steps(session_factory, registry, "load")

    with _client(session_factory, registry) as client:
        idle = _run_bar(client.get("/ui/management").text)
        response = client.post("/ui/management/purge", follow_redirects=False)
        run_id = registry.runs_of("monitor")[0].id
        with session_factory.begin() as session:
            session.execute(
                text(
                    "UPDATE operator_operation_runs SET status = 'running', started_at = now(), "
                    "heartbeat_at = now(), stderr = :log WHERE id = :id"
                ),
                {
                    "id": run_id,
                    "log": "event=junk_purge_progress articles=500 total=16046 persons=120 reviews=0\n"
                    "event=junk_purge_progress articles=1000 total=16046 persons=260 reviews=0\n",
                },
            )
        page = client.get(response.headers["location"]).text

    assert (
        'id="step-purge" class="step current" type="submit" formaction="/ui/management/purge"'
        in idle
    )
    run = registry.get(run_id)
    assert (run.parameters.mode, run.command[2:]) == ("purge", ["purge-junk"])
    assert "Очистка от мусора" in page
    assert '<progress class="overall" value="1000" max="16046">' in page
    assert "Людей удалено: 260" in page
    assert f'formaction="/ui/management/runs/{run_id}/stop"' in _run_bar(page)


@pytest.mark.parametrize(
    ("finished", "status", "current"),
    [
        ((), "succeeded", "load"),
        (("load",), "succeeded", "purge"),
        (("load",), "failed", "purge"),  # one broken source does not block the cycle
        (("load", "purge"), "failed", "purge"),  # a crashed purge is repeated
        (("load", "purge"), "interrupted", "purge"),
        (("load", "purge", "entities"), "succeeded", "figurants"),
        (("load", "purge", "entities", "figurants"), "succeeded", "political"),
        (("load", "purge", "entities", "figurants", "political"), "succeeded", "load"),
        (("load", "purge", "entities", "figurants"), "failed", "figurants"),  # a crash is repeated
        (("load", "purge", "entities", "rosfin"), "succeeded", "figurants"),  # legacy run
        # An old standalone check that failed or was lost is not repeated: the final step
        # checks the list anyway.
        (("load", "purge", "entities", "rosfin"), "failed", "figurants"),
        (("load", "purge", "entities", "rosfin"), "interrupted", "figurants"),
        (("load", "purge", "entities", "resolve"), "succeeded", "load"),
        (("load", "resolve"), "failed", "load"),  # a resolution ends the cycle
    ],
)
def test_the_current_step_follows_the_latest_run(
    session_factory: sessionmaker[Session], finished: tuple[str, ...], status: str, current: str
) -> None:
    registry = OperationRegistry(session_factory, executor=lambda _work: None)
    if finished:
        finish_steps(session_factory, registry, *finished[:-1])
        finish_steps(session_factory, registry, finished[-1], status=status)

    with _client(session_factory, registry) as client:
        bar = _run_bar(client.get("/ui/management").text)

    assert re.findall(r'id="step-(\w+)" class="step current', bar) == [current]
    assert bar.count(" disabled") >= 2  # every other step is grey


def test_the_entities_step_starts_from_management(
    session_factory: sessionmaker[Session],
) -> None:
    registry = OperationRegistry(session_factory, executor=lambda _work: None)
    finish_steps(session_factory, registry, "load", "purge")

    with _client(session_factory, registry) as client:
        response = client.post("/ui/management/entities", follow_redirects=False)
        page = client.get(response.headers["location"]).text

    run = registry.runs_of("monitor")[0]
    assert (run.parameters.mode, run.command[2:]) == ("entities", ["collect-entities"])
    assert "Сборка сущностей" in page and "Готовлюсь" in page


def test_the_final_step_includes_rosfinmonitoring_check(
    session_factory: sessionmaker[Session],
) -> None:
    registry = OperationRegistry(session_factory, executor=lambda _work: None)
    finish_steps(session_factory, registry, "load", "purge", "entities", "figurants")

    with _client(session_factory, registry) as client:
        bar = _run_bar(client.get("/ui/management").text)
        response = client.post("/ui/management/political", follow_redirects=False)
        run_id = registry.runs_of("monitor")[0].id
        with session_factory.begin() as session:
            session.execute(
                text(
                    "UPDATE operator_operation_runs SET status = 'succeeded', stdout = :out "
                    "WHERE id = :id"
                ),
                {
                    "id": run_id,
                    "out": '{"snapshot_id": 2, "snapshot_date": "2026-09-25T00:00:00+00:00", '
                    '"entries": 22950, "new_snapshot": true, "download_error": null, '
                    '"entities": 10421, "rf_full": 3605, "rf_possible": 950, '
                    '"rf_merged": 12, "region_merged": 7}',
                },
            )
        page = client.get(response.headers["location"]).text

    assert 'id="step-political" class="step current" type="submit"' in bar
    run = registry.get(run_id)
    assert (run.parameters.mode, run.command[2:]) == ("political", ["find-political"])
    assert "Политические дела и сверка с РФМ" in page
    assert "Сверено по снимку перечня РФМ #2 от 2026-09-25" in page
    assert "В перечне (ФИО с отчеством): 3605" in page
    assert "Возможно в перечне: 950" in page


def test_step_four_finds_the_figurants_from_management(
    session_factory: sessionmaker[Session],
) -> None:
    registry = OperationRegistry(session_factory, executor=lambda _work: None)
    finish_steps(session_factory, registry, "load", "purge", "entities")

    with _client(session_factory, registry) as client:
        bar = _run_bar(client.get("/ui/management").text)
        response = client.post("/ui/management/figurants", follow_redirects=False)
        run_id = registry.runs_of("monitor")[0].id
        with session_factory.begin() as session:
            session.execute(
                text(
                    "UPDATE operator_operation_runs SET status = 'running', started_at = now(), "
                    "heartbeat_at = now(), stderr = :log WHERE id = :id"
                ),
                {"id": run_id, "log": "event=entity_figurants_stage stage=asking 50/3200\n"},
            )
        running = client.get(response.headers["location"]).text
        with session_factory.begin() as session:
            session.execute(
                text(
                    "UPDATE operator_operation_runs SET status = 'succeeded', stdout = :out WHERE id = :id"
                ),
                {
                    "id": run_id,
                    "out": '{"entities": 6850, "figurant_rules": 3650, "figurant_model": 1200, '
                    '"possible": 90, "mentioned": 1700, "unclear": 210, "asked_now": 3200, '
                    '"cached": 0, "failures": 10}',
                },
            )
        done = client.get(response.headers["location"]).text

    assert 'id="step-figurants" class="step current" type="submit"' in bar
    run = registry.get(run_id)
    assert (run.parameters.mode, run.command[2:]) == ("figurants", ["find-figurants"])
    assert '<progress class="overall" value="50" max="3200">' in running
    assert "Фигуранты по статье УК без ответа модели: 3650" in done
    assert "Фигуранты по ответу модели: 1200" in done
    assert "Только упомянуты: 1700" in done
    assert "Модель не ответила: 10" in done


def test_step_six_finds_the_political_cases_from_management(
    session_factory: sessionmaker[Session],
) -> None:
    registry = OperationRegistry(session_factory, executor=lambda _work: None)
    finish_steps(session_factory, registry, "load", "purge", "entities", "figurants")

    with _client(session_factory, registry) as client:
        bar = _run_bar(client.get("/ui/management").text)
        response = client.post("/ui/management/political", follow_redirects=False)
        run_id = registry.runs_of("monitor")[0].id
        with session_factory.begin() as session:
            session.execute(
                text(
                    "UPDATE operator_operation_runs SET status = 'succeeded', stdout = :out "
                    "WHERE id = :id"
                ),
                {
                    "id": run_id,
                    "out": '{"rf_full": 3605, "rf_possible": 950, "figurants": 4831, '
                    '"political_rules": 1378, "political_model": 1500, '
                    '"criminal": 1800, "unclear": 150, "asked_now": 3450, "cached": 0, '
                    '"failures": 3}',
                },
            )
        done = client.get(response.headers["location"]).text

    assert 'id="step-political" class="step current" type="submit"' in bar
    run = registry.get(run_id)
    assert (run.parameters.mode, run.command[2:]) == ("political", ["find-political"])
    assert "Политические по статье УК: 1378" in done
    assert "Политические по ответу модели: 1500" in done
    assert "В перечне (ФИО с отчеством): 3605" in done
    assert "Уголовные: 1800" in done and 'href="/ui/political">Результат</a>' in done


def test_the_home_page_shows_the_selection_funnel(session_factory: sessionmaker[Session]) -> None:
    with session_factory.begin() as session:
        seed = ResearchSeeder(session)
        ovd = seed.source("ОВД-Инфо", "https://ovd.info")
        for day, external_id in (
            (datetime(2025, 3, 4, tzinfo=UTC), "old"),
            (datetime(2026, 9, 1, tzinfo=UTC), "new"),
        ):
            seed.article(ovd, external_id=external_id, title="Суд", text="Текст", published_at=day)
    registry = OperationRegistry(session_factory, executor=lambda _work: None)

    with _client(session_factory, registry) as client:
        page = client.get("/ui/management").text

    assert "<h2>Воронка отбора</h2>" in page
    steps = re.findall(r'<span class="funnel-step">(\d)</span>', page)
    assert steps == ["1", "2", "3", "4", "5"]
    assert 'class="funnel-stage result" href="/ui/political"' in page
    # The funnel counts every publication: it says from when to when.
    assert "<b>За всё время:</b> публикации с 04.03.2025 по 01.09.2026." in page


def test_the_latest_manual_runs_are_four_at_most(session_factory: sessionmaker[Session]) -> None:
    registry = OperationRegistry(session_factory, executor=lambda _work: None)
    ids = finish_steps(
        session_factory, registry, "load", "purge", "entities", "figurants", "political"
    )

    with _client(session_factory, registry) as client:
        page = client.get("/ui/management").text

    listed = [
        int(run_id) for run_id in re.findall(r'<a href="/ui/management\?run_id=(\d+)">#', page)
    ]
    # Five runs: the four latest, newest first.
    assert listed == ids[:0:-1]


def test_a_live_old_list_check_is_named_as_what_it_is(
    session_factory: sessionmaker[Session],
) -> None:
    registry = OperationRegistry(session_factory, executor=lambda _work: None)
    finish_steps(session_factory, registry, "load", "purge", "entities")
    finish_steps(session_factory, registry, "rosfin", status="running")

    with _client(session_factory, registry) as client:
        bar = _run_bar(client.get("/ui/management").text)

    # Its stop button stands on the next step, and says which run it stops.
    assert "Остановить: Сверка с РФМ (прежний отдельный шаг)" in bar
    assert "Найти фигурантов</button>" not in bar


@pytest.mark.parametrize(
    ("out", "shown", "hidden"),
    [
        (
            (
                '{"snapshot_id": 3, "snapshot_date": "2026-09-25T00:00:00+00:00", '
                '"download_error": "ConnectTimeout: timed out", "rf_full": 3, "figurants": 1}'
            ),
            [
                (
                    "Свежий перечень не скачан, сверено по последнему сохранённому снимку: "
                    "ConnectTimeout: timed out"
                ),
                "Сверено по снимку перечня РФМ #3 от 2026-09-25",
            ],
            ["Сверка с РФМ не выполнена"],
        ),
        (
            '{"snapshot_id": null, "download_error": "ConnectTimeout: timed out", "figurants": 1}',
            ["Сверка с РФМ не выполнена: снимков перечня нет. Политичность оценена без неё."],
            ["Сверено по снимку"],
        ),
        (
            (
                '{"snapshot_id": null, "rf_error": "RuntimeError: the database went away", '
                '"figurants": 1}'
            ),
            ["Сверка с РФМ не выполнена: RuntimeError: the database went away."],
            ["Сверено по снимку"],
        ),
    ],
)
def test_the_final_card_says_how_the_list_was_checked(
    session_factory: sessionmaker[Session], out: str, shown: list[str], hidden: list[str]
) -> None:
    registry = OperationRegistry(session_factory, executor=lambda _work: None)
    finish_steps(session_factory, registry, "load", "purge", "entities", "figurants")

    with _client(session_factory, registry) as client:
        response = client.post("/ui/management/political", follow_redirects=False)
        run_id = registry.runs_of("monitor")[0].id
        with session_factory.begin() as session:
            session.execute(
                text(
                    "UPDATE operator_operation_runs SET status = 'succeeded', stdout = :out "
                    "WHERE id = :id"
                ),
                {"id": run_id, "out": out},
            )
        page = client.get(response.headers["location"]).text

    for text_ in shown:
        assert text_ in page
    for text_ in hidden:
        assert text_ not in page
    # On the list is who a person is: no red mark.
    assert '<span class="badge failed">В перечне' not in page
