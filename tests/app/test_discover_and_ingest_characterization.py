"""Characterization of `discover-and-ingest --source ovd-info`, as wired by the CLI.

Pins what the command hands to persistence and prints, end to end through the real
listing parser, retrying fetcher and article parser. Only the network and the
database are replaced: the listing comes from a MockTransport, the article pages from
a fixture-serving fetcher standing in for `WebsiteAdapter`, and the SQLAlchemy
persistence from a recorder.
"""

import argparse
import asyncio
import hashlib
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar, cast

import httpx
import pytest

from cli.context import CliContext
from cli.ingestion import run_discover_and_ingest
from monitor_core.errors import TransientFetchError
from monitor_core.model import ParsedArticle, PersistenceResult, RawDocument, SourceReference

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
LISTING_HTML = (FIXTURES / "ovd_info_listing.html").read_bytes()
ARTICLE_HTML = (FIXTURES / "ovd_info_article.html").read_bytes()
BROKEN_HTML = b"<html><body><p>no title here</p></body></html>"
FETCHED_AT = datetime(2026, 9, 13, 12, 0, tzinfo=UTC)

FIRST = "/express-news/2026/09/11/minyust-isklyuchil-evgeniya-ponasenkova-iz-reestra-inoagentov"
SECOND = "/express-news/2026/09/11/razrabotchik-vyvez-iz-rossii-vnutrennie-dokumenty-o-slezhke-fsb-chto-iz"
THIRD = "/express-news/2026/09/02/v-moskve-zaderzhali-glavu-fonda-dom-s-mayakom-lidu-moniavu-pered-etim-u-nee"


class RecordingPersistence:
    instances: ClassVar[list["RecordingPersistence"]] = []

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.saved: list[tuple[RawDocument, ParsedArticle]] = []
        RecordingPersistence.instances.append(self)

    def save(self, raw_document: RawDocument, article: ParsedArticle) -> PersistenceResult:
        self.saved.append((raw_document, article))
        return PersistenceResult(document_id=100 + len(self.saved), article_id=len(self.saved))


class FixtureWebsiteAdapter:
    """First article: the fixture. Second: a page without a title. Third: the fixture,
    after one transient failure the retrying fetcher absorbs."""

    calls: ClassVar[list[str]] = []

    async def fetch(self, reference: SourceReference) -> RawDocument:
        FixtureWebsiteAdapter.calls.append(reference.external_id)
        if reference.external_id == THIRD and FixtureWebsiteAdapter.calls.count(THIRD) == 1:
            raise TransientFetchError("HTTP 503")
        content = BROKEN_HTML if reference.external_id == SECOND else ARTICLE_HTML
        return RawDocument(
            external_id=reference.external_id,
            url=reference.url,
            fetched_at=FETCHED_AT,
            content_type="text/html; charset=UTF-8",
            content=content,
        )


@pytest.fixture
def wired_cli(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    RecordingPersistence.instances = []
    FixtureWebsiteAdapter.calls = []
    listing_requests: list[str] = []

    def handle_request(request: httpx.Request) -> httpx.Response:
        listing_requests.append(str(request.url))
        return httpx.Response(200, content=LISTING_HTML, request=request)

    original_async_client = httpx.AsyncClient

    def create_client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(handle_request)
        return original_async_client(*args, **kwargs)

    async def no_sleep(_delay: float) -> None:
        return None

    monkeypatch.setattr("cli.ingestion.httpx.AsyncClient", create_client)
    monkeypatch.setattr("cli.ingestion.SqlAlchemyIngestionPersistence", RecordingPersistence)
    monkeypatch.setattr("cli.ingestion.WebsiteAdapter", FixtureWebsiteAdapter)
    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    return listing_requests


def test_ovd_info_discover_and_ingest_hands_persistence_the_same_documents(
    wired_cli: list[str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    session_factory = object()
    context = cast(CliContext, SimpleNamespace(session_factory=session_factory))

    run_discover_and_ingest(argparse.Namespace(limit=3, source="ovd-info"), context)

    assert wired_cli == ["https://ovd.info/express-news"]
    assert FixtureWebsiteAdapter.calls == [FIRST, SECOND, THIRD, THIRD]

    [persistence] = RecordingPersistence.instances
    assert persistence.kwargs == {
        "session_factory": session_factory,
        "source_name": "ОВД-Инфо",
        "source_base_url": "https://ovd.info",
    }

    assert [raw.external_id for raw, _ in persistence.saved] == [FIRST, THIRD]
    for raw, article in persistence.saved:
        assert raw.url == f"https://ovd.info{raw.external_id}"
        assert raw.fetched_at == FETCHED_AT
        assert raw.content_type == "text/html; charset=UTF-8"
        assert raw.content == ARTICLE_HTML
        assert article.external_id == raw.external_id
        assert article.url == raw.url
        assert article.title == "На комика Сашу Долгополова завели дело о реабилитации нацизма"
        assert article.published_at == datetime.fromisoformat("2026-08-13T17:57:00+03:00")
        assert len(article.text) == 475
        assert article.text.startswith(
            "На ходатайство о заочном аресте обратила внимание «Медиазона». "
            "Дело возбудили по статье о реабилитации нацизма.\n\n"
        )
        assert (
            hashlib.sha256(article.text.encode()).hexdigest()
            == "56add2cf4b3c1cebcce12b3dbe1f88a812702c0fb72786c44de6d47344217ed1"
        )

    assert capsys.readouterr().out.splitlines() == [
        f"saved: https://ovd.info{FIRST} document_id=101",
        f"saved: https://ovd.info{THIRD} document_id=102",
        f"failed: https://ovd.info{SECOND} Title not found",
        "completed: 2 saved, 1 failed",
    ]
