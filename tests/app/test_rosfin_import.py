"""Bringing the published Rosfinmonitoring list in from a file, through the console.

The site is the usual source; this is for when it cannot be reached. The file arrives
as the raw request body, so nothing here needs a multipart dependency, and the same file
twice must leave one snapshot.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from api import app, get_db
from db.orm_models import RosfinmonitoringEntryRecord, RosfinmonitoringSnapshotRecord
from web.routers.rosfinmonitoring_import import RF_LIST_URL

IMPORT_URL = "/api/admin/rosfinmonitoring/import"

LIST_PAGE = """<!doctype html><html>
<div class="panel-heading"><h4>Физические лица</h4></div>
<div class="panel-body"><ol>
<li>1. ОРЛОВ ИВАН *, 01.01.1970 г.р. , Г. МОСКВА;</li>
<li>2. ПЕТРОВ ПЁТР *, 02.02.1980 г.р. , Г. Казань;</li>
</ol></div>
</html>""".encode()


@pytest.fixture
def client(session_factory: sessionmaker[Session]) -> Iterator[TestClient]:
    def override_get_db() -> Iterator[Session]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)


def _snapshots(session_factory: sessionmaker[Session]) -> int:
    with session_factory() as session:
        return session.scalar(select(func.count()).select_from(RosfinmonitoringSnapshotRecord))


def _entries(session_factory: sessionmaker[Session]) -> int:
    with session_factory() as session:
        return session.scalar(select(func.count()).select_from(RosfinmonitoringEntryRecord))


class TestImport:
    def test_a_list_file_becomes_a_snapshot(
        self, client: TestClient, session_factory: sessionmaker[Session]
    ) -> None:
        response = client.post(
            IMPORT_URL, content=LIST_PAGE, headers={"Content-Type": "application/octet-stream"}
        )

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "imported"
        assert body["entries"] == 2
        assert body["snapshot_id"] is not None
        assert body["source_url"] == RF_LIST_URL
        assert body["snapshot_date"]
        # Nobody has been re-matched against it yet, and the page has to say so.
        assert body["rematch_required"] is True
        assert _snapshots(session_factory) == 1
        assert _entries(session_factory) == 2

    def test_the_same_file_twice_leaves_one_snapshot(
        self, client: TestClient, session_factory: sessionmaker[Session]
    ) -> None:
        client.post(IMPORT_URL, content=LIST_PAGE)
        second = client.post(IMPORT_URL, content=LIST_PAGE).json()

        # Reported, not written again: a list is recognised by its content.
        assert second["status"] == "unchanged"
        assert _snapshots(session_factory) == 1
        assert _entries(session_factory) == 2

    def test_a_changed_file_becomes_a_second_snapshot(
        self, client: TestClient, session_factory: sessionmaker[Session]
    ) -> None:
        client.post(IMPORT_URL, content=LIST_PAGE)
        changed = LIST_PAGE.replace(b"02.02.1980", b"03.03.1990")
        second = client.post(IMPORT_URL, content=changed).json()

        assert second["status"] == "imported"
        assert _snapshots(session_factory) == 2
        # The older snapshot is left alone: snapshots are history, not a rolling window.
        assert _entries(session_factory) == 4

    def test_an_empty_body_is_refused(self, client: TestClient) -> None:
        response = client.post(IMPORT_URL, content=b"")
        assert response.status_code == 422
        assert "пуст" in response.json()["detail"]

    def test_a_file_that_is_not_a_list_is_reported_not_stored(
        self, client: TestClient, session_factory: sessionmaker[Session]
    ) -> None:
        response = client.post(
            IMPORT_URL, content=b"\xd0\xbf\xd1\x80\xd0\xb8\xd0\xb2\xd0\xb5\xd1\x82"
        )

        assert response.status_code == 200
        assert response.json()["status"] == "error"
        assert _snapshots(session_factory) == 0

    def test_a_file_over_the_ceiling_is_refused(self, client: TestClient) -> None:
        from web.routers.rosfinmonitoring_import import MAX_UPLOAD_BYTES

        response = client.post(IMPORT_URL, content=b"x" * (MAX_UPLOAD_BYTES + 1))
        assert response.status_code == 413


class TestPage:
    def test_the_page_offers_the_file_picker_and_shows_the_current_list(
        self, client: TestClient
    ) -> None:
        page = client.get("/ui/airtable")
        assert page.status_code == 200
        assert 'id="official-file"' in page.text
        assert 'id="import-button"' in page.text
        assert IMPORT_URL in page.text
        # Nothing loaded yet, and the page says so rather than staying silent.
        assert "Перечень ещё не загружен" in page.text

    def test_the_page_names_the_snapshot_in_use(
        self, client: TestClient, session_factory: sessionmaker[Session]
    ) -> None:
        client.post(IMPORT_URL, content=LIST_PAGE)
        page = client.get("/ui/airtable")
        assert "Снимок #1" in page.text
        assert "записей 2" in page.text

    def test_the_page_warns_that_matching_must_follow(self, client: TestClient) -> None:
        assert "сверить с РФМ" in client.get("/ui/airtable").text

    def test_the_script_reads_the_endpoint_from_the_page(self, client: TestClient) -> None:
        assert "/static/airtable-sync.js" in client.get("/ui/airtable").text
