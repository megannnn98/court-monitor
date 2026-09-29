"""«О системе»: the build stamp the console runs on, and the totals it counts.

Local fixtures only — no database, no network, no embeddings.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from web.app import app
from web.build_info import APP_VERSION, UNKNOWN, build_info
from web.dependencies import get_db


@contextmanager
def _client(session_factory: sessionmaker[Session]) -> Iterator[TestClient]:
    def override() -> Iterator[Session]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)


# The build stamp


def test_a_stamped_build_says_which_commit_it_is() -> None:
    info = build_info({"BUILD_COMMIT": "04499dfabc1234def", "BUILD_TIME": "2026-09-29T18:00:00Z"})

    assert info.commit == "04499dfabc12", "a short hash is enough and fits the page"
    assert info.built_at == "2026-09-29T18:00:00Z"
    assert info.version == APP_VERSION


def test_a_working_copy_says_it_does_not_know() -> None:
    info = build_info({})

    assert info.commit == UNKNOWN
    assert info.built_at == UNKNOWN


def test_the_stamp_degrades_instead_of_guessing() -> None:
    assert build_info({"BUILD_COMMIT": "   "}).commit == UNKNOWN
    assert build_info({"BUILD_COMMIT": "04499df"}).commit == "04499df"


def test_every_field_is_a_row_for_the_page() -> None:
    rows = build_info({"BUILD_COMMIT": "04499df"}).rows()

    assert [name for name, _ in rows] == ["Версия приложения", "Коммит", "Собран"]


# The page


def test_the_page_shows_the_commit_and_the_counts(session_factory) -> None:
    with _client(session_factory) as client:
        page = client.get("/ui/about").text

    assert "О системе" in page
    assert "Версия приложения" in page
    assert "Публикаций в базе" in page
    assert "Людей в базе" in page
    # The same tables the status strip counts.
    assert "parsed_articles" in page
    assert "entity_groups" in page


def test_the_page_is_in_the_menu(session_factory) -> None:
    with _client(session_factory) as client:
        page = client.get("/ui/about").text

    assert 'href="/ui/about"' in page
    assert "О системе" in page


def test_the_page_decides_nothing(session_factory) -> None:
    with _client(session_factory) as client:
        page = client.get("/ui/about").text

    assert "<form" not in page, "a read-only page carries no forms"
    assert 'method="post"' not in page
    assert "Очистить" not in page and "Удалить" not in page


def test_the_totals_match_the_database(session_factory) -> None:
    with session_factory.begin() as session:
        session.execute(
            text(
                "INSERT INTO parsed_articles (document_id, title, text) "
                "SELECT 1, 't', 'x' WHERE false"
            )
        )
    before = 0
    with session_factory() as session:
        before = session.execute(text("SELECT count(*) FROM parsed_articles")).scalar() or 0

    with _client(session_factory) as client:
        page = client.get("/ui/about").text

    assert f"{before:,}".replace(",", " ") in page


def test_an_empty_run_table_does_not_break_the_page(session_factory) -> None:
    with session_factory.begin() as session:
        session.execute(text("DELETE FROM operator_operation_runs"))

    with _client(session_factory) as client:
        page = client.get("/ui/about").text

    assert "Последний успешный запуск" in page
    assert UNKNOWN in page


@pytest.mark.parametrize("path", ["/ui/about"])
def test_the_page_needs_no_query_parameters(session_factory, path: str) -> None:
    with _client(session_factory) as client:
        assert client.get(path).status_code == 200
