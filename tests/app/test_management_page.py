"""Manual source selection starts one recorded monitoring operation."""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker
from support.research_db_fixtures import ResearchSeeder

from db.orm_models import OperatorOperationRunRecord, SourceDocument
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


def test_management_page_lists_only_news_sources_and_selects_all(
    session_factory: sessionmaker[Session],
) -> None:
    registry = OperationRegistry(session_factory, executor=lambda _work: None)

    with _client(session_factory, registry) as client:
        response = client.get("/ui/management")

    assert response.status_code == 200
    assert 'href="/ui/management">Управление</a>' in response.text
    assert 'id="toggle-all-sources" type="checkbox" checked' in response.text
    checkboxes = re.findall(
        r'<input type="checkbox" name="sources" value="([^"]+)" (checked)?>', response.text
    )
    listed_sources = {name for name, _checked in checkboxes}
    assert listed_sources == {source.name for source in news_sources()}
    assert all(checked == "checked" for _name, checked in checkboxes)
    assert "memopzk-figurants" not in listed_sources


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
    assert "sota-vision" in run_page.text and "ovd-info" in run_page.text
    assert "Ждут очереди: 2" in run_page.text
    with session_factory() as session:
        assert session.get(OperatorOperationRunRecord, run_id) is not None


def test_empty_or_registry_source_selection_does_not_start_a_run(
    session_factory: sessionmaker[Session],
) -> None:
    registry = OperationRegistry(session_factory, executor=lambda _work: None)

    with _client(session_factory, registry) as client:
        empty = client.post("/ui/management/run", data={})
        registry_source = client.post("/ui/management/run", data={"sources": ["memopzk-figurants"]})

    assert empty.status_code == 400
    assert "Выберите хотя бы один" in empty.text
    assert registry_source.status_code == 400
    assert "не новостной источник" in registry_source.text
    assert registry.runs_of("monitor") == []


def _source_row(page: str, source: str) -> str:
    match = re.search(
        rf'<tr data-kind="[a-z]+" data-search="[^"]*">(?:(?!</tr>).)*value="{re.escape(source)}"'
        r"(?:(?!</tr>).)*</tr>",
        page,
    )
    assert match is not None, source
    return match.group(0)


def test_the_source_table_shows_when_each_source_last_loaded_and_its_articles(
    session_factory: sessionmaker[Session],
) -> None:
    now = datetime.now(UTC)
    with session_factory.begin() as session:
        seed = ResearchSeeder(session)
        sota = seed.source("SOTA", "https://sota.vision")
        seed.article(sota, external_id="sota-1", title="Суд", text="Текст")
        ovd = seed.source("ОВД-Инфо", "https://ovd.info")
        for index in range(2):
            seed.article(ovd, external_id=f"ovd-{index}", title="Задержание", text="Текст")
        # Catch-up loads never write the monitoring checkpoint: the fetch time is the truth.
        for document in session.scalars(select(SourceDocument)).all():
            document.fetched_at = now - (
                timedelta(hours=2) if document.external_id == "sota-1" else timedelta(days=10)
            )
    registry = OperationRegistry(session_factory, executor=lambda _work: None)

    with _client(session_factory, registry) as client:
        page = client.get("/ui/management").text

    fresh, stale, never = (
        _source_row(page, "sota-vision"),
        _source_row(page, "ovd-info"),
        _source_row(page, "tg-mash"),
    )
    assert '<td class="">' in fresh and '<td class="num">1</td>' in fresh
    # Ten days without a fetched document is flagged; never is flagged louder.
    assert '<td class="stale">' in stale and '<td class="num">2</td>' in stale
    assert '<td class="never">никогда</td><td class="num">0</td>' in never


def test_every_source_has_a_filterable_kind(session_factory: sessionmaker[Session]) -> None:
    registry = OperationRegistry(session_factory, executor=lambda _work: None)

    with _client(session_factory, registry) as client:
        page = client.get("/ui/management").text

    assert _source_row(page, "ovd-info").startswith('<tr data-kind="site"')
    assert _source_row(page, "tg-mash").startswith('<tr data-kind="telegram"')
    assert _source_row(page, "tg-moscowcourts").startswith('<tr data-kind="court"')
    assert f'data-kind="all">Все ({len(news_sources())})</button>' in page
    assert 'id="source-search"' in page


def test_the_run_button_counts_the_selection_and_is_off_without_one(
    session_factory: sessionmaker[Session],
) -> None:
    registry = OperationRegistry(session_factory, executor=lambda _work: None)

    with _client(session_factory, registry) as client:
        full = client.get("/ui/management").text
        empty = client.post("/ui/management/run", data={}).text

    total = len(news_sources())
    assert (
        f'<button id="run-button" class="run-button" type="submit" >Подгрузить статьи '
        f'(<span class="selected-count">{total}</span>)</button>'
    ) in full
    assert (
        '<button id="resolve-button" class="run-button secondary" type="submit" '
        f'formaction="/ui/management/resolve" >Разрешить персоны '
        f'(<span class="selected-count">{total}</span>)</button>'
    ) in full
    assert '<button id="run-button" class="run-button" type="submit" disabled>' in empty
    assert 'formaction="/ui/management/resolve" disabled>' in empty


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


def test_resolve_starts_person_resolution_of_the_selected_sources(
    session_factory: sessionmaker[Session],
) -> None:
    registry = OperationRegistry(session_factory, executor=lambda _work: None)

    with _client(session_factory, registry) as client:
        page = client.get("/ui/management")
        response = client.post(
            "/ui/management/resolve",
            data={"sources": ["sota-vision", "ovd-info"]},
            follow_redirects=False,
        )
        run_page = client.get(response.headers["location"])

    assert 'formaction="/ui/management/resolve"' in page.text
    assert response.status_code == 303
    run = registry.runs_of("monitor")[0]
    assert run.parameters.mode == "resolve"
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


def test_load_and_resolution_cannot_run_at_once(session_factory: sessionmaker[Session]) -> None:
    registry = OperationRegistry(session_factory, executor=lambda _work: None)

    with _client(session_factory, registry) as client:
        client.post("/ui/management/run", data={"sources": ["ovd-info"]}, follow_redirects=False)
        second = client.post("/ui/management/resolve", data={"sources": ["ovd-info"]})

    assert second.status_code == 409
    assert "уже выполняется" in second.text


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
    # The source selection follows the card, not dozens of rows later.
    assert page.index("</details>") < page.index('id="source-table"')


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
