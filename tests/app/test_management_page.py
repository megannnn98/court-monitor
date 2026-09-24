"""Manual source selection starts one recorded monitoring operation."""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from support.research_db_fixtures import ResearchSeeder

from db.orm_models import OperatorOperationRunRecord, SourceDocument
from operator_console import OperationRegistry, OperationRunStatus
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
    assert run.command[-6:] == [
        "--selected-source",
        "sota-vision",
        "--selected-source",
        "ovd-info",
        "--limit",
        "50",
    ]
    assert len(queued) == 1
    assert "Запуск #" in run_page.text
    assert "sota-vision" in run_page.text and "ovd-info" in run_page.text
    assert "Ожидает запуска" in run_page.text
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
        f'<button id="run-button" type="submit" >Подгрузить статьи '
        f'(<span id="selected-count">{total}</span>)</button>'
    ) in full
    assert '<button id="run-button" type="submit" disabled>' in empty


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
