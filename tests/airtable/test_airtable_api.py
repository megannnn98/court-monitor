"""The sync endpoint and «База Airtable»: `POST /api/admin/airtable/sync` and `/api/v1/airtable`."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from support.airtable_fakes import CONFIGURED_ENV, FakeAirtable, fake_source

from airtable.client import AirtableError, AirtableRecord
from airtable.service import AirtableSyncService
from analysis.storage.models import AirtableKnownPersonRecord
from api import app, get_db
from processing.storage.models import ExcludedPersonRecord

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
    # The endpoint builds the client itself from the configuration, so this is the seam:
    # the constructor every sync goes through.
    monkeypatch.setattr("airtable.service.HttpAirtableClient", lambda _settings: fake)
    return fake


class TestEndpoint:
    def test_the_sync_reports_every_table(self, client: TestClient, airtable: FakeAirtable) -> None:
        airtable.tables["Known"] = [AirtableRecord("recK1", {"full_name": "Иван Иванов"})]

        response = client.post(SYNC_URL)

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "success"
        assert sorted(body["tables"]) == [
            "articles",
            "known_persons",
            "officials",
            "sources",
        ]
        assert body["tables"]["known_persons"] == {
            "created": 1,
            "updated": 0,
            "unchanged": 0,
            "errors": 0,
            "received": 1,
            "status": "success",
            "error": None,
            # Said out loud, because a list that quietly lost rows is one nobody trusts.
            "removed": 0,
            "removed_blocked": None,
        }
        assert body["started_at"] and body["finished_at"]

    def test_a_second_sync_reports_no_duplicates(
        self, client: TestClient, airtable: FakeAirtable
    ) -> None:
        airtable.tables["Excluded"] = [AirtableRecord("recE1", {"full_name": "Ольга Минакова"})]

        client.post(SYNC_URL)
        second = client.post(SYNC_URL).json()

        assert second["tables"]["officials"]["created"] == 0
        assert second["tables"]["officials"]["unchanged"] == 1

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
        assert body["tables"]["officials"]["created"] == 1
        assert body["tables"]["officials"]["error"] is None

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

    def test_the_configured_probe_reports_the_mode_not_the_token(self, client: TestClient) -> None:
        body = client.get("/api/admin/airtable/configured").json()
        assert set(body) == {"configured", "mode", "detail"}
        assert CONFIGURED_ENV["AIRTABLE_TOKEN"] not in str(body)


API_PAGE = "/api/v1/airtable"


class TestPage:
    """«База Airtable» as the React page reads it, and its button through the API."""

    def test_the_page_offers_the_button(self, client: TestClient, airtable: FakeAirtable) -> None:
        page = client.get(API_PAGE)

        assert page.status_code == 200
        assert page.json()["configured"] is True and page.json()["reason"] == ""

    def test_the_button_is_absent_and_the_reason_shown_when_unconfigured(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        for name in CONFIGURED_ENV:
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setenv("AIRTABLE_IMPORT_DIR", str(tmp_path))

        page = client.get(API_PAGE).json()

        assert page["configured"] is False and page["reason"]
        # The reason names what to set.
        assert "AIRTABLE_TOKEN" in json.dumps(page, ensure_ascii=False)
        # The token is in no answer, configured or not.
        assert CONFIGURED_ENV["AIRTABLE_TOKEN"] not in json.dumps(page)

    def test_the_page_names_the_configured_airtable_tables(
        self, client: TestClient, airtable: FakeAirtable
    ) -> None:
        page = json.dumps(client.get(API_PAGE).json(), ensure_ascii=False)
        for table in CONFIGURED_ENV.values():
            if table.startswith("app") or table == "secret-token":
                continue
            assert table in page
        assert CONFIGURED_ENV["AIRTABLE_TOKEN"] not in page

    def test_the_page_lists_every_reference_list(
        self, client: TestClient, airtable: FakeAirtable
    ) -> None:
        airtable.tables["Known"] = [AirtableRecord("recK1", {"full_name": "Иван Иванов"})]
        client.post(SYNC_URL)

        lists = client.get(API_PAGE).json()["lists"]

        # The four lists of the sync; the Rosfinmonitoring list is the page's own section.
        assert [item["label"] for item in lists] == [
            "Источники",
            "Найденные люди",
            "Должностные лица",
            "Статьи",
        ]

    def test_the_button_syncs(self, client: TestClient, airtable: FakeAirtable) -> None:
        airtable.tables["Known"] = [AirtableRecord("recK1", {"full_name": "Иван Иванов"})]

        response = client.post("/api/v1/airtable/sync")

        assert response.status_code == 200 and response.json()["status"] == "success"

    def test_the_button_refuses_a_running_sync(
        self, client: TestClient, airtable: FakeAirtable, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        @contextmanager
        def held() -> Iterator[bool]:
            yield False

        monkeypatch.setattr(AirtableSyncService, "_guard", lambda _self: held())

        assert client.post("/api/v1/airtable/sync").status_code == 409

    def test_the_button_says_so_when_unconfigured(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for name in CONFIGURED_ENV:
            monkeypatch.delenv(name, raising=False)

        assert client.post("/api/v1/airtable/sync").status_code == 503


def test_the_lists_are_readable_afterwards(
    session_factory: sessionmaker[Session], airtable: FakeAirtable
) -> None:
    """The pipeline's side of the contract: the rows are in PostgreSQL afterwards."""
    airtable.tables["Known"] = [AirtableRecord("recK1", {"full_name": "Иван Иванов"})]
    airtable.tables["Excluded"] = [AirtableRecord("recE1", {"full_name": "Ольга Минакова"})]

    service = AirtableSyncService(session_factory, fake_source(airtable))
    service.sync()

    with session_factory() as session:
        assert session.scalars(select(AirtableKnownPersonRecord)).all()
        assert session.scalars(select(ExcludedPersonRecord)).all()


class TestShareLinksMode:
    """The page in the mode the operator actually runs it: public links, no token.

    The API mode was covered and this one was not, and the page answered 500 in it —
    it had a note for every mode except the one that matters.
    """

    def test_the_page_is_served_in_share_mode(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("AIRTABLE_TOKEN", "")
        monkeypatch.setenv("AIRTABLE_SHARE_URL_RFM", "https://airtable.com/appA/shrB")

        page = client.get(API_PAGE)

        assert page.status_code == 200
        assert "Публичная ссылка" in json.dumps(page.json(), ensure_ascii=False)

    def test_the_page_says_how_the_lists_are_read(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("AIRTABLE_TOKEN", "")
        monkeypatch.setenv("AIRTABLE_SHARE_URL_RFM", "https://airtable.com/appA/shrB")

        page = json.dumps(client.get(API_PAGE).json(), ensure_ascii=False)

        assert "без токена и без выгрузки вручную" in page
        assert "https://airtable.com/appA/shrB" in page
