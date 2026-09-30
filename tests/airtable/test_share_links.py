"""Reading a public Airtable share link, and the Rosfinmonitoring list, with no token.

The point of these tests is not the happy path — the protocol was verified against the
live site — but the ways it fails *quietly*. A share read that returns an empty list
instead of an error, or a 200 that is the application's own page, would empty a list in
the database while the report said everything was fine. Each test below stands for one of
those ways.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from airtable.client import AirtableError
from airtable.links import FallbackTableClient, ShareTableClient, share_links
from airtable.models import MODE_SHARE, TABLES
from airtable.service import build_sync_source
from airtable.share import _init_data, content_id, read_page, read_records, share_url_of
from rosfinmonitoring.download import (
    RosfinmonitoringDownloadError,
    download_rf_list,
)

RF_LINK = "https://airtable.com/appExample0000001/shrExample0000003"
SOURCES_LINK = "https://airtable.com/appExample0000002/shrExample0000001"
ARTICLES_LINK = "https://airtable.com/appExample0000001/shrExample0000002"


def _page(policy: str = '{"signature":"abc","expires":"2026-10-22T00:00:00.000Z"}') -> str:
    """A share page as the browser gets it: the data sits in one raw assignment."""
    return (
        "<html><script>window.initData = "
        + json.dumps(
            {
                "sharedViewId": "viwABC",
                "applicationIdOfInitialPageLoadForLogging": "appExample0000001",
                "pageLoadId": "pglXYZ",
                "accessPolicy": policy,
            }
        )
        + ";</script><script>window.initData = {}</script></html>"
    )


class _Response:
    def __init__(
        self,
        status_code: int = 200,
        text: str = "",
        content: bytes = b"",
        content_type: str = "text/html",
    ) -> None:
        self.status_code = status_code
        self.text = text
        self.content = content or text.encode()
        self.headers = {"content-type": content_type}


class _Session:
    """A stand-in for a browser session: answers by URL, remembers what it was asked."""

    def __init__(self, page: str | None = None, csv: bytes = b"", csv_type: str = "text/csv"):
        self._page = page
        self._csv = csv
        self._csv_type = csv_type
        self.calls: list[str] = []
        self.params: dict[str, Any] | None = None
        self.headers: dict[str, str] | None = None

    def get(self, url: str, *, params=None, headers=None, timeout=None):
        self.calls.append(url)
        self.params = params
        self.headers = headers
        if "downloadCsv" in url:
            if self._csv_type != "text/csv":
                return _Response(200, "<html>приложение</html>", content_type=self._csv_type)
            return _Response(200, content=self._csv, content_type="text/csv")
        if self._page is None:
            return _Response(404, "нет")
        return _Response(200, self._page)


def test_the_share_link_names_the_base_and_the_share() -> None:
    assert share_url_of(RF_LINK) == ("appExample0000001", "shrExample0000003")
    assert share_url_of(SOURCES_LINK)[0] == "appExample0000002"
    assert share_url_of("https://example.com/table") is None


def test_the_page_yields_the_view_and_the_signed_policy() -> None:
    page = read_page(RF_LINK, session=_Session(page=_page()))

    assert page.view_id == "viwABC"
    assert "signature" in page.access_policy
    assert page.page_load_id == "pglXYZ"


def test_the_policy_is_passed_as_it_came() -> None:
    """Airtable signs it. Signing it again here would produce a policy Airtable refuses,
    and the symptom of getting that wrong is a 200 that is not CSV."""
    session = _Session(page=_page(), csv="ФИО,Дата\nИванов Иван,01.01.1980\n".encode())

    read_records(RF_LINK, session=session)

    assert session.params is not None
    assert json.loads(session.params["accessPolicy"])["signature"] == "abc"


def test_the_rows_come_back_as_records() -> None:
    csv = 'Преследуемый,Дата рождения\nИванов Иван,"July 12, 1980"\n'.encode()

    records = read_records(RF_LINK, session=_Session(page=_page(), csv=csv))

    assert len(records) == 1
    assert records[0].fields["Преследуемый"] == "Иванов Иван"
    assert records[0].fields["Дата рождения"] == "July 12, 1980"


def test_the_bom_does_not_eat_the_first_column() -> None:
    """Airtable marks its exports with a BOM. Left in, it becomes part of the first
    column's name and that column silently stops being readable."""
    csv = "﻿Преследуемый,Дата рождения\nИванов Иван,01.01.1980\n".encode()

    records = read_records(RF_LINK, session=_Session(page=_page(), csv=csv))

    assert records[0].fields["Преследуемый"] == "Иванов Иван"


def test_a_page_that_is_not_csv_is_an_error_not_an_empty_list() -> None:
    """The failure this guards against: without the policy in the query string the
    request falls through to Airtable's own page, which is a 200. Read as "no rows" it
    empties a list and the report says all is well."""
    session = _Session(page=_page(), csv_type="text/html")

    with pytest.raises(AirtableError, match="не CSV"):
        read_records(RF_LINK, session=session)


def test_a_link_that_is_gone_is_an_error() -> None:
    with pytest.raises(AirtableError, match="вернула 404"):
        read_page(
            "https://airtable.com/appAAAAAAAAAAAAAAAA/shrAAAAAAAAAAAAAAAA", session=_Session()
        )


def test_a_page_that_grants_nothing_is_an_error() -> None:
    page = (
        "<html>window.initData = "
        + json.dumps({"applicationIdOfInitialPageLoadForLogging": "appX"})
        + ";</html>"
    )

    with pytest.raises(AirtableError, match="не даёт права"):
        read_page(RF_LINK, session=_Session(page=page))


def test_an_empty_view_never_reaches_the_sync_as_an_empty_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A view that has no rows is a mistake worth reporting, not a list to write. Applied
    as it comes, it would empty 13 765 people and report every one of them unchanged."""
    from airtable import share

    monkeypatch.setattr(share, "read_records", lambda link, **kwargs: [])
    client = ShareTableClient({"rfm_persons": RF_LINK})

    with pytest.raises(AirtableError, match="ни одной строки"):
        client.list_records("rfm_persons")


def test_a_list_with_no_link_is_skipped_rather_than_read_as_empty() -> None:
    client = ShareTableClient({"rfm_persons": RF_LINK})

    with pytest.raises(FileNotFoundError, match="ссылка не настроена"):
        client.list_records("officials")


def test_the_known_people_list_reads_the_rosfinmonitoring_link() -> None:
    """«Найденные люди» and «Росфинмониторинг» are the same 13 765 people, so one link
    serves both; naming the list once is enough."""
    links = share_links({"AIRTABLE_SHARE_URL_RFM": RF_LINK})

    assert links["rfm_persons"] == RF_LINK
    assert links["known_persons"] == RF_LINK
    assert "officials" not in links


def test_a_known_people_link_of_its_own_wins() -> None:
    links = share_links(
        {"AIRTABLE_SHARE_URL_RFM": RF_LINK, "AIRTABLE_SHARE_URL_KNOWN": ARTICLES_LINK}
    )

    assert links["known_persons"] == ARTICLES_LINK


def test_share_links_win_over_files_when_no_token_is_set() -> None:
    env = {"AIRTABLE_SHARE_URL_SOURCES": SOURCES_LINK, "AIRTABLE_SHARE_URL_RFM": RF_LINK}

    source = build_sync_source(env)

    assert source.mode == MODE_SHARE
    assert set(source.tables) == {"sources", "rfm_persons", "known_persons"}


class _DeadToken:
    """A token that is valid but has no rights to the base: the API answers 401 for
    every table, which is what happens with a base owned by someone else."""

    def list_records(self, table: str):
        raise AirtableError(f"Airtable answered 401 for table {table!r}")


def test_a_token_without_rights_does_not_break_the_lists() -> None:
    """One dead token would otherwise take every list down with it, while the same lists
    stay readable through their public links."""
    client = FallbackTableClient(
        _DeadToken(),
        share_links(
            {"AIRTABLE_SHARE_URL_SOURCES": SOURCES_LINK, "AIRTABLE_SHARE_URL_RFM": RF_LINK}
        ),
        {
            "sources": "Sources",
            "rfm_persons": "Persons",
            "known_persons": "Persons",
            "officials": "Officials",
        },
    )
    client._links = _FakeLinks()  # type: ignore[attr-defined]

    assert client.list_records("Sources")
    assert client.list_records("Persons")
    assert client.used_links == {"sources", "rfm_persons"}


def test_a_list_with_no_link_reports_the_api_failure_itself() -> None:
    """There is nothing to fall back to, so the API's own answer is the honest one."""
    client = FallbackTableClient(_DeadToken(), share_links({}), {"officials": "Officials"})

    with pytest.raises(AirtableError, match="401"):
        client.list_records("Officials")


class _FakeLinks:
    """Stands in for the share reader so the fallback is tested without a network."""

    def __init__(self) -> None:
        self.links = {"sources": SOURCES_LINK, "rfm_persons": RF_LINK}

    def list_records(self, name: str):
        from airtable.client import AirtableRecord

        return [AirtableRecord("share:1", {"full_name": "Иванов Иван"})]


class _RefusingLinks:
    def __init__(self) -> None:
        self.links = {"sources": SOURCES_LINK}

    def list_records(self, name: str):
        raise AirtableError("ссылка отозвана")


def test_a_link_that_stops_answering_is_reported_as_such() -> None:
    client = FallbackTableClient(_DeadToken(), {}, {"sources": "Sources"})
    client._links = _RefusingLinks()  # type: ignore[attr-defined]

    with pytest.raises(AirtableError, match="ни API, ни публичная ссылка"):
        client.list_records("Sources")


def test_two_lists_on_one_table_resolve_the_same_way() -> None:
    """«Росфинмониторинг» and «Найденные люди» are one table. Which list the name means
    is decided in reading order, not by which entry a dict kept last."""
    client = FallbackTableClient(
        _DeadToken(), {}, {"rfm_persons": "Persons", "known_persons": "Persons"}
    )
    client._links = _FakeLinks()  # type: ignore[attr-defined]
    client._links.links = {"rfm_persons": RF_LINK}

    client.list_records("Persons")

    assert client.used_links == {"rfm_persons"}


def test_the_init_data_object_is_parsed_whatever_follows_it() -> None:
    """Its length moves between requests, so it cannot be sliced to a fixed end."""
    page = "<script>window.initData = " + json.dumps({"a": 1}) + ";window.other = 2;</script>"

    assert _init_data(page) == {"a": 1}
    assert _init_data("<html>nothing here</html>") is None


class _RfResponse:
    def __init__(self, status_code: int, body: bytes) -> None:
        self.status_code = status_code
        self.content = body
        self.headers: dict[str, str] = {}


def test_the_published_list_is_downloaded(monkeypatch: pytest.MonkeyPatch) -> None:
    from rosfinmonitoring import download

    class _Client:
        @staticmethod
        def get(url, **kwargs):
            assert kwargs.get("impersonate") == download.IMPERSONATE
            return _RfResponse(
                200,
                "<html><title>Росфинмониторинг</title>"
                "1. ИВАНОВ ИВАН, 01.01.1980 г.р.</html>".encode(),
            )

    monkeypatch.setattr("curl_cffi.requests", _Client)

    assert "ИВАНОВ".encode() in download_rf_list("https://example.test/list")


def test_a_403_is_not_a_list(monkeypatch: pytest.MonkeyPatch) -> None:
    """The site answers 403 to anything that is not a browser. Read as an empty list it
    would replace a snapshot of 23 000 people with nothing."""

    class _Client:
        @staticmethod
        def get(url, **kwargs):
            return _RfResponse(403, b"forbidden")

    monkeypatch.setattr("curl_cffi.requests", _Client)

    with pytest.raises(RosfinmonitoringDownloadError, match="вернул 403"):
        download_rf_list("https://example.test/list")


def test_a_200_that_is_not_the_list_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """A maintenance page or a captcha is a 200 too, and would parse as a list of nobody.

    And so is a page that says «Росфинмониторинг» and lists nobody — which is exactly what
    `fedsfm.ru/documents/terr-list` serves while the list itself lives one address over.
    Matching on any one marker would have taken that page for the list and emptied the
    snapshot of 23 000 people.
    """

    class _Client:
        @staticmethod
        def get(url, **kwargs):
            return _RfResponse(200, b"<html><h1>Technical work</h1></html>")

    monkeypatch.setattr("curl_cffi.requests", _Client)

    with pytest.raises(RosfinmonitoringDownloadError, match="это не перечень"):
        download_rf_list("https://example.test/list")


def test_the_five_lists_are_the_ones_the_operator_named() -> None:
    """Sources, the Rosfinmonitoring list, the people already known, the officials, and
    the articles to watch for — in the order they are read on the page."""
    assert TABLES == ("sources", "rfm_persons", "known_persons", "officials", "articles")


class TestRowIdentity:
    """A row's identity is what it says, not where it stands.

    This is the whole reason the id is not a row number. These lists are edited by hand,
    all the time; one person inserted in the middle must not turn fourteen thousand
    people into strangers.
    """

    _PEOPLE = ("Преследуемый", "Дата рождения")

    def test_the_same_person_keeps_the_same_id(self) -> None:
        first = content_id(
            "rfm_persons",
            {"Преследуемый": "Иванов Иван", "Дата рождения": "1980-07-12"},
            self._PEOPLE,
        )
        again = content_id(
            "rfm_persons",
            {"Преследуемый": "  иванов иван ", "Дата рождения": "1980-07-12"},
            self._PEOPLE,
        )

        assert first == again

    def test_a_different_birth_date_is_a_different_person(self) -> None:
        """The pair is what tells two namesakes apart, so it is part of the identity."""
        one = content_id(
            "rfm_persons",
            {"Преследуемый": "Иванов Иван", "Дата рождения": "1980-07-12"},
            self._PEOPLE,
        )
        other = content_id(
            "rfm_persons",
            {"Преследуемый": "Иванов Иван", "Дата рождения": "1990-01-01"},
            self._PEOPLE,
        )

        assert one != other

    def test_inserting_somebody_else_does_not_rename_anybody(self) -> None:
        """The failure this prevents: with the row's number as the id, one insertion in
        the middle shifts every id after it, and a list nobody touched comes back as
        fourteen thousand updates."""
        before = content_id(
            "rfm_persons",
            {"Преследуемый": "Петров Пётр", "Дата рождения": "1970-01-01"},
            self._PEOPLE,
        )
        # Somebody is inserted above him in Airtable; his fields are unchanged.
        after = content_id(
            "rfm_persons",
            {"Преследуемый": "Петров Пётр", "Дата рождения": "1970-01-01"},
            self._PEOPLE,
        )

        assert before == after

    def test_the_id_says_which_list_it_came_from(self) -> None:
        fields = {"Полная статья": "159 УК РФ"}
        article = content_id("articles", fields, ("Полная статья",))
        person = content_id("rfm_persons", {"Преследуемый": "159 УК РФ"}, ("Преследуемый",))

        assert article != person
