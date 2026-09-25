"""The customer's console keeps candidates, manual management and the wiki."""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker
from support.research_db_fixtures import ResearchSeeder

from api import app, get_db
from db.orm_models import RosfinmonitoringSnapshotRecord
from web.candidate_rows import latest_snapshot_id

REMOVED_PAGES = [
    ("GET", "/ui/person-resolution/reviews"),
    ("GET", "/ui/person-resolution/reviews/1"),
    ("POST", "/ui/person-resolution/reviews/1/decision"),
    ("GET", "/ui/search"),
    ("GET", "/ui/cases"),
    ("GET", "/ui/cases/export.xlsx"),
    ("GET", "/ui/candidates/export"),
    ("GET", "/ui/candidates/export.pdf"),
    ("GET", "/ui/channel"),
    ("GET", "/ui/monitoring"),
    ("GET", "/ui/operations"),
    ("GET", "/ui/operations/monitor"),
    ("POST", "/ui/operations/monitor/confirm"),
    ("GET", "/ui/operations/runs/1"),
]


@contextmanager
def _client(session_factory: sessionmaker[Session]) -> Iterator[TestClient]:
    def override_get_db() -> Iterator[Session]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_the_menu_hides_the_candidates_for_now(session_factory: sessionmaker[Session]) -> None:
    with _client(session_factory) as client:
        page = client.get("/ui/candidates")

    nav = re.search(r"<nav>(.*?)</nav>", page.text, re.DOTALL)
    assert nav is not None
    links = re.findall(r'href="([^"]+)"><svg[^>]*>.*?</svg><span>([^<]+)</span>', nav.group(1))
    # The home page first, apart; «Результат» under it, with its count; then the tools.
    assert 'class="home" href="/ui/management"><svg class="icon"' in nav.group(1)
    assert '<span>Результат</span><span class="nav-count">0</span>' in nav.group(1)
    assert '<div class="nav-group">Инструменты</div>' in nav.group(1)
    # Officials, logs and wiki at the bottom, apart.
    bottom = re.search(r'<div class="nav-bottom">(.*?)</div>', nav.group(1), re.DOTALL)
    assert bottom is not None
    assert re.findall(r"<span>([^<]+)</span>", bottom.group(1)) == [
        "Должностные лица",
        "Логи",
        "Вики",
    ]
    assert links == [
        ("/ui/management", "Главная: Управление"),
        ("/ui/political", "Результат"),
        ("/ui/entities", "Сущности"),
        ("/ui/disputes", "Спорные случаи"),
        ("/ui/officials", "Должностные лица"),
        ("/ui/logs", "Логи"),
        ("/ui/wiki", "Вики"),
    ]


@pytest.mark.parametrize(("method", "path"), REMOVED_PAGES)
def test_a_removed_page_is_not_found(
    session_factory: sessionmaker[Session], method: str, path: str
) -> None:
    with _client(session_factory) as client:
        response = client.request(method, path, follow_redirects=False)

    assert response.status_code == 404


def test_the_site_opens_on_its_home_page(session_factory: sessionmaker[Session]) -> None:
    with _client(session_factory) as client:
        console = client.get("/ui", follow_redirects=False)
        site = client.get("/", follow_redirects=False)
        page = client.get("/ui/entities").text

    assert (console.status_code, console.headers["location"]) == (303, "/ui/management")
    assert (site.status_code, site.headers["location"]) == (303, "/ui/management")
    assert '<a class="brand" href="/ui/management">court-monitor</a>' in page


def test_the_candidates_the_wiki_and_their_exports_are_served(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        snapshot_id = ResearchSeeder(session).snapshot()
        session.commit()

    with _client(session_factory) as client:
        responses = {
            path: client.get(path)
            for path in (
                "/ui/candidates",
                f"/ui/candidates/export.xlsx?snapshot_id={snapshot_id}",
                "/ui/wiki",
                "/ui/wiki/Local-Web-UI",
                "/ui/wiki/export.pdf",
            )
        }

    assert {path: response.status_code for path, response in responses.items()} == {
        path: 200 for path in responses
    }


def test_the_json_api_behind_the_removed_pages_stays(
    session_factory: sessionmaker[Session],
) -> None:
    """Only user interfaces went away: the API they called is still registered."""
    with session_factory() as session:
        snapshot_id = ResearchSeeder(session).snapshot()
        session.commit()

    with _client(session_factory) as client:
        statuses = {
            path: client.get(path).status_code
            for path in (
                f"/candidates?snapshot_id={snapshot_id}",
                "/operations/runs",
                "/monitoring/status",
                "/person-resolution/reviews",
                "/health/live",
            )
        }

    assert statuses == dict.fromkeys(statuses, 200)


def test_the_page_and_the_bot_open_on_the_newest_snapshot(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        seed = ResearchSeeder(session)
        older = seed.snapshot("older-hash")
        newer = seed.snapshot("newer-hash")
        session.get_one(RosfinmonitoringSnapshotRecord, older).snapshot_date = datetime(
            2026, 8, 1, tzinfo=UTC
        )
        session.get_one(RosfinmonitoringSnapshotRecord, newer).snapshot_date = datetime(
            2026, 9, 1, tzinfo=UTC
        )
        session.commit()

    with session_factory() as session:
        assert latest_snapshot_id(session) == newer
    with _client(session_factory) as client:
        page = client.get("/ui/candidates")

    assert f'<option value="{newer}" selected>' in page.text
