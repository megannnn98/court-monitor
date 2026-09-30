"""The sync endpoint and the button: `POST /api/admin/airtable/sync` and `/ui/airtable`."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from support.airtable_fakes import CONFIGURED_ENV, FakeAirtable

from airtable.client import AirtableError, AirtableRecord
from airtable.config import AirtableSettings
from airtable.service import AirtableSyncService
from api import app, get_db
from db.orm_models import AirtableKnownPersonRecord, ExcludedPersonRecord

SYNC_URL = "/api/admin/airtable/sync"


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


@pytest.fixture
def airtable(monkeypatch: pytest.MonkeyPatch) -> FakeAirtable:
    """Airtable as the handler will build it: configured, and answered by a fake."""
    for name, value in CONFIGURED_ENV.items():
        monkeypatch.setenv(name, value)
    fake = FakeAirtable({})
    monkeypatch.setattr(AirtableSyncService, "_new_client", lambda _self: fake)
    return fake


class TestEndpoint:
    def test_the_sync_reports_every_table(self, client: TestClient, airtable: FakeAirtable) -> None:
        airtable.tables["Known"] = [AirtableRecord("recK1", {"full_name": "Иван Иванов"})]

        response = client.post(SYNC_URL)

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "success"
        assert sorted(body["tables"]) == [
            "excluded_persons",
            "known_persons",
            "rfm_persons",
            "sources",
        ]
        assert body["tables"]["known_persons"] == {
            "created": 1,
            "updated": 0,
            "unchanged": 0,
            "errors": 0,
            "received": 1,
            "error": None,
        }
        assert body["started_at"] and body["finished_at"]

    def test_a_second_sync_reports_no_duplicates(
        self, client: TestClient, airtable: FakeAirtable
    ) -> None:
        airtable.tables["Excluded"] = [AirtableRecord("recE1", {"full_name": "Ольга Минакова"})]

        client.post(SYNC_URL)
        second = client.post(SYNC_URL).json()

        assert second["tables"]["excluded_persons"]["created"] == 0
        assert second["tables"]["excluded_persons"]["unchanged"] == 1

    def test_a_failing_table_is_named_and_the_rest_still_report(
        self, client: TestClient, airtable: FakeAirtable
    ) -> None:
        airtable.tables["Known"] = AirtableError("Airtable answered 403 for table 'Known'")
        airtable.tables["Excluded"] = [AirtableRecord("recE1", {"full_name": "Ольга Минакова"})]

        response = client.post(SYNC_URL)

        # 200 with a partial status, not a 500: one broken base is not a failed sync.
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "partial"
        assert "Known" in body["tables"]["known_persons"]["error"]
        assert body["tables"]["excluded_persons"]["created"] == 1
        assert body["tables"]["excluded_persons"]["error"] is None

    def test_an_unconfigured_airtable_is_a_readable_error(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for name in CONFIGURED_ENV:
            monkeypatch.delenv(name, raising=False)

        response = client.post(SYNC_URL)

        assert response.status_code == 503
        assert "AIRTABLE_TOKEN" in response.json()["detail"]

    def test_a_second_sync_at_once_is_refused_with_409(
        self, client: TestClient, airtable: FakeAirtable, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        @contextmanager
        def held() -> Iterator[bool]:
            yield False

        monkeypatch.setattr(AirtableSyncService, "_guard", lambda _self: held())

        response = client.post(SYNC_URL)

        assert response.status_code == 409
        assert response.json()["detail"] == "Airtable synchronization is already running"
        # Refused before any table is read: the second press costs nothing.
        assert airtable.requested == []

    def test_the_response_never_carries_the_token(
        self, client: TestClient, airtable: FakeAirtable
    ) -> None:
        assert CONFIGURED_ENV["AIRTABLE_TOKEN"] not in client.post(SYNC_URL).text

    def test_the_configured_probe_reports_only_a_flag(self, client: TestClient) -> None:
        body = client.get("/api/admin/airtable/configured").json()
        assert set(body) == {"configured"}
        assert CONFIGURED_ENV["AIRTABLE_TOKEN"] not in str(body)


class TestPage:
    def test_the_page_offers_the_button(self, client: TestClient, airtable: FakeAirtable) -> None:
        page = client.get("/ui/airtable")

        assert page.status_code == 200
        assert "Синхронизировать Airtable" in page.text
        assert 'id="sync-button"' in page.text
        # The script paints the result in place, so the page is never reloaded.
        assert "/static/airtable-sync.js" in page.text
        assert SYNC_URL in page.text

    def test_the_button_is_absent_and_the_reason_shown_when_unconfigured(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for name in CONFIGURED_ENV:
            monkeypatch.delenv(name, raising=False)

        page = client.get("/ui/airtable")

        assert "Синхронизировать Airtable" in page.text
        assert 'id="sync-button"' not in page.text
        assert "Airtable не настроен" in page.text
        assert "AIRTABLE_TOKEN" in page.text

    def test_the_config_the_script_reads_is_valid_json(
        self, client: TestClient, airtable: FakeAirtable
    ) -> None:
        """A `application/json` block is not HTML-escaped: escaping it here would hand
        the script `&quot;` and every click would fail silently."""
        import json
        import re

        page = client.get("/ui/airtable").text
        block = re.search(
            r'<script type="application/json" id="airtable-sync-config">(.*?)</script>',
            page,
            re.DOTALL,
        )
        assert block is not None
        config = json.loads(block.group(1))
        assert config["url"] == SYNC_URL
        assert list(config["labels"].values()) == [
            "Источники",
            "Росфинмониторинг",
            "Найденные люди",
            "Исключения",
        ]
        assert config["order"] == [
            "sources",
            "rfm_persons",
            "known_persons",
            "excluded_persons",
        ]
        # The token is not on the page in any form.
        assert CONFIGURED_ENV["AIRTABLE_TOKEN"] not in page

    def test_the_page_names_the_configured_airtable_tables(
        self, client: TestClient, airtable: FakeAirtable
    ) -> None:
        page = client.get("/ui/airtable")
        for table in CONFIGURED_ENV.values():
            if table.startswith("app") or table == "secret-token":
                continue
            assert table in page.text

    def test_the_page_lists_all_four_reference_lists(
        self, client: TestClient, airtable: FakeAirtable
    ) -> None:
        airtable.tables["Known"] = [AirtableRecord("recK1", {"full_name": "Иван Иванов"})]
        client.post(SYNC_URL)

        page = client.get("/ui/airtable")

        for label in ("Источники", "Росфинмониторинг", "Найденные люди", "Исключения"):
            assert label in page.text

    def test_the_button_is_in_the_navigation(
        self, client: TestClient, airtable: FakeAirtable
    ) -> None:
        assert "/ui/airtable" in client.get("/ui/airtable").text

    def test_the_no_javascript_form_syncs_and_comes_back(
        self, client: TestClient, airtable: FakeAirtable
    ) -> None:
        airtable.tables["Known"] = [AirtableRecord("recK1", {"full_name": "Иван Иванов"})]

        response = client.post("/ui/airtable/sync", follow_redirects=False)

        assert response.status_code == 303
        assert response.headers["Location"] == "/ui/airtable?status=success"

    def test_the_no_javascript_form_refuses_a_running_sync(
        self, client: TestClient, airtable: FakeAirtable, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        @contextmanager
        def held() -> Iterator[bool]:
            yield False

        monkeypatch.setattr(AirtableSyncService, "_guard", lambda _self: held())

        assert client.post("/ui/airtable/sync", follow_redirects=False).status_code == 409

    def test_the_no_javascript_form_says_so_when_unconfigured(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for name in CONFIGURED_ENV:
            monkeypatch.delenv(name, raising=False)

        assert client.post("/ui/airtable/sync", follow_redirects=False).status_code == 503


def test_the_lists_are_readable_afterwards(
    session_factory: sessionmaker[Session], airtable: FakeAirtable
) -> None:
    """The pipeline's side of the contract: the rows are in PostgreSQL afterwards."""
    airtable.tables["Known"] = [AirtableRecord("recK1", {"full_name": "Иван Иванов"})]
    airtable.tables["Excluded"] = [AirtableRecord("recE1", {"full_name": "Ольга Минакова"})]

    service = AirtableSyncService(
        session_factory, AirtableSettings.from_env(CONFIGURED_ENV), client=airtable
    )
    service.sync()

    with session_factory() as session:
        assert session.scalars(select(AirtableKnownPersonRecord)).all()
        assert session.scalars(select(ExcludedPersonRecord)).all()
