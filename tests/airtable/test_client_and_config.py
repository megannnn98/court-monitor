"""Airtable client, configuration and lock: no database, no network, no real Airtable."""

from __future__ import annotations

import threading
from collections.abc import Callable

import httpx
import pytest
from sqlalchemy import Engine

from airtable.client import AirtableError, AirtableRecord, HttpAirtableClient
from airtable.config import AirtableConfigurationError, AirtableSettings
from db.database import create_database_engine
from db.locks import try_advisory_lock

_ENV = {
    "AIRTABLE_TOKEN": "secret-token",
    "AIRTABLE_BASE_ID": "appTest",
    "AIRTABLE_SOURCES_TABLE": "Sources",
    "AIRTABLE_RFM_PERSONS_TABLE": "RFM",
    "AIRTABLE_KNOWN_PERSONS_TABLE": "Known",
    "AIRTABLE_OFFICIALS_TABLE": "Excluded",
}


def _settings(**overrides: str) -> AirtableSettings:
    return AirtableSettings.from_env({**_ENV, **overrides})


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> HttpAirtableClient:
    return HttpAirtableClient(
        _settings(), client=httpx.Client(transport=httpx.MockTransport(handler))
    )


class TestConfiguration:
    def test_all_six_variables_are_required(self) -> None:
        assert AirtableSettings.from_env(_ENV).base_id == "appTest"

    @pytest.mark.parametrize("missing", sorted(_ENV))
    def test_a_blank_or_absent_variable_is_a_readable_error(self, missing: str) -> None:
        env = {**_ENV, missing: ""}
        with pytest.raises(AirtableConfigurationError) as caught:
            AirtableSettings.from_env(env)
        # The operator is told which variable, not shown a stack trace.
        assert missing in str(caught.value)

    def test_is_configured_never_raises(self) -> None:
        assert AirtableSettings.is_configured(_ENV) is True
        assert AirtableSettings.is_configured({}) is False

    def test_a_timeout_must_be_a_positive_number(self) -> None:
        with pytest.raises(AirtableConfigurationError):
            _settings(AIRTABLE_TIMEOUT_SECONDS="soon")
        with pytest.raises(AirtableConfigurationError):
            _settings(AIRTABLE_TIMEOUT_SECONDS="0")

    def test_the_token_is_never_in_the_redacted_settings(self) -> None:
        redacted = str(_settings().redacted())
        assert "secret-token" not in redacted
        assert "appTest" in redacted


class TestHttpClient:
    def test_records_are_read_with_the_token_in_the_header_only(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json={"records": [{"id": "rec1", "fields": {"a": 1}}]})

        records = _client(handler).list_records("Sources")
        assert [record.id for record in records] == ["rec1"]
        assert records[0].text("a") == "1"
        assert seen[0].headers["Authorization"] == "Bearer secret-token"
        # The token must never reach a URL, a log line or a proxy.
        assert "secret-token" not in str(seen[0].url)

    def test_pagination_follows_the_offset(self) -> None:
        pages = [
            {"records": [{"id": "rec1", "fields": {}}], "offset": "cursor-1"},
            {"records": [{"id": "rec2", "fields": {}}]},
        ]

        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=pages.pop(0))

        assert [record.id for record in _client(handler).list_records("RFM")] == ["rec1", "rec2"]
        assert not pages

    @pytest.mark.parametrize("status", [401, 403, 404, 429, 500])
    def test_an_http_error_names_the_table_and_never_the_token(self, status: int) -> None:
        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(status, json={"error": "nope"})

        with pytest.raises(AirtableError) as caught:
            _client(handler).list_records("Known")
        assert "Known" in str(caught.value)
        assert "secret-token" not in str(caught.value)

    def test_an_unreachable_api_is_an_airtable_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("no route", request=request)

        with pytest.raises(AirtableError):
            _client(handler).list_records("Excluded")

    def test_unreadable_json_is_an_airtable_error(self) -> None:
        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"<html>oops</html>")

        with pytest.raises(AirtableError):
            _client(handler).list_records("Sources")

    def test_a_record_without_an_id_is_refused(self) -> None:
        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"records": [{"fields": {}}]})

        with pytest.raises(AirtableError):
            _client(handler).list_records("Sources")

    def test_an_empty_table_is_no_records(self) -> None:
        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"records": []})

        assert _client(handler).list_records("Sources") == []


class TestRecord:
    def test_a_field_is_found_under_any_of_its_names(self) -> None:
        record = AirtableRecord("rec1", {"ФИО": "  Иван Иванов  "})
        assert record.text("full_name", "ФИО") == "Иван Иванов"

    def test_a_missing_field_is_empty(self) -> None:
        assert AirtableRecord("rec1", {}).text("name") == ""

    def test_an_absent_checkbox_keeps_the_default(self) -> None:
        assert AirtableRecord("rec1", {}).flag("active") is True
        assert AirtableRecord("rec1", {}).flag("active", default=False) is False
        assert AirtableRecord("rec1", {"active": False}).flag("active") is False


class TestAdvisoryLock:
    def test_a_held_key_is_refused_to_a_second_holder(self, test_engine: Engine) -> None:
        with try_advisory_lock(test_engine, "test:airtable") as first:
            assert first is True
            # A second engine, as a second worker in another process would open.
            other = create_database_engine(test_engine.url.render_as_string(hide_password=False))
            try:
                with try_advisory_lock(other, "test:airtable") as second:
                    assert second is False
            finally:
                other.dispose()

    def test_the_key_is_released_on_exit(self, test_engine: Engine) -> None:
        with try_advisory_lock(test_engine, "test:airtable"):
            pass
        with try_advisory_lock(test_engine, "test:airtable") as again:
            assert again is True

    def test_a_different_key_is_unaffected(self, test_engine: Engine) -> None:
        with (
            try_advisory_lock(test_engine, "test:airtable"),
            try_advisory_lock(test_engine, "test:other") as other,
        ):
            assert other is True

    def test_two_threads_racing_for_one_key_produce_exactly_one_winner(
        self, test_engine: Engine
    ) -> None:
        started = threading.Barrier(2)
        outcomes: list[bool] = []
        guard = threading.Lock()

        def attempt() -> None:
            started.wait(timeout=5)
            with try_advisory_lock(test_engine, "test:airtable") as acquired, guard:
                outcomes.append(acquired)

        threads = [threading.Thread(target=attempt) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        assert sorted(outcomes) == [False, True]
