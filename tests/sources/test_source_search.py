"""The read-only search of a configured source, over a fake transport: no site is asked.

What is held: a search asks only its own channel's page; words travel only as `q`;
a transient failure is retried, a refusal is not; a channel outside the registry
cannot be configured. The live check is opt-in: `INVESTIGATION_LIVE_SEARCH=1`.
"""

from __future__ import annotations

import asyncio
import logging
import os
from urllib.parse import quote, quote_plus

import httpx
import pytest

from ingestion.search import (
    SearchAnswerError,
    SearchConfigurationError,
    SourceSearchQuery,
    TelegramChannelSearch,
    configured_telegram_channels,
)
from ingestion.sources.telegram.channels import TelegramChannel
from investigation.models import InvestigationQuery
from investigation.services.external import SourceSearchRetriever
from monitor_core.retry import transient_http_error

CHANNEL = TelegramChannel(username="ovdinfolive", title="ОВД-Инфо LIVE", topics=("Новости",))


def page(*posts: tuple[str, int, str]) -> bytes:
    bubbles = "".join(
        f"""
        <div class="tgme_widget_message" data-post="{channel}/{number}">
          <div class="tgme_widget_message_text">{text}</div>
          <a class="tgme_widget_message_date"><time datetime="2026-09-25T07:00:14+00:00"></time></a>
        </div>"""
        for channel, number, text in posts
    )
    return f"<html><body>{bubbles}</body></html>".encode()


def search_over(handler: httpx.MockTransport) -> TelegramChannelSearch:
    client = httpx.AsyncClient(transport=handler)
    return TelegramChannelSearch(CHANNEL, client=client, base_delay_seconds=0)


def test_a_search_asks_its_channels_page_with_the_words_as_q_and_reads_its_own_posts() -> None:
    asked: list[httpx.URL] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(request.url)
        return httpx.Response(
            200,
            content=page(
                ("ovdinfolive", 43913, "Адвоката Марию Бонцлер не этапировали"),
                ("otherchannel", 5, "Репост чужого канала"),
                ("ovdinfolive", 43595, "Судья удалил адвоката Марию Бонцлер"),
            ),
        )

    found = asyncio.run(
        search_over(httpx.MockTransport(handler)).search(SourceSearchQuery("Бонцлер", limit=5))
    )

    (url,) = asked
    assert (url.scheme, url.host, url.path) == ("https", "t.me", "/s/ovdinfolive")
    assert url.params["q"] == "Бонцлер"
    assert [item.external_id for item in found] == ["43913", "43595"]
    assert found[0].url == "https://t.me/ovdinfolive/43913"
    assert found[0].source == "tg-ovdinfolive" and found[0].published_at is not None


@pytest.mark.parametrize(
    "words",
    [
        "http://127.0.0.1:8080/admin",
        "file:///etc/passwd",
        "../../evil?x=1#frag",
        "a&q=other&before=1",
    ],
)
def test_words_never_become_an_address(words: str) -> None:
    asked: list[httpx.URL] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(request.url)
        return httpx.Response(200, content=page())

    asyncio.run(search_over(httpx.MockTransport(handler)).search(SourceSearchQuery(words)))

    (url,) = asked
    assert (url.scheme, url.host, url.path) == ("https", "t.me", "/s/ovdinfolive")
    assert list(url.params.keys()) == ["q"] and url.params["q"] == words


def test_a_transient_failure_is_retried_and_a_refusal_is_not() -> None:
    statuses = iter([503, 200])

    def flaky(request: httpx.Request) -> httpx.Response:
        return httpx.Response(next(statuses), content=page(("ovdinfolive", 1, "текст")))

    assert (
        len(asyncio.run(search_over(httpx.MockTransport(flaky)).search(SourceSearchQuery("x"))))
        == 1
    )

    calls = 0

    def forbidden(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(403)

    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(search_over(httpx.MockTransport(forbidden)).search(SourceSearchQuery("x")))
    assert calls == 1


@pytest.mark.parametrize(
    ("failure", "retried"),
    [
        (httpx.ReadTimeout("slow"), True),
        (httpx.ConnectError("refused"), True),
        (
            httpx.HTTPStatusError(
                "", request=httpx.Request("GET", "https://t.me"), response=httpx.Response(429)
            ),
            True,
        ),
        (
            httpx.HTTPStatusError(
                "", request=httpx.Request("GET", "https://t.me"), response=httpx.Response(502)
            ),
            True,
        ),
        (
            httpx.HTTPStatusError(
                "", request=httpx.Request("GET", "https://t.me"), response=httpx.Response(400)
            ),
            False,
        ),
        (
            httpx.HTTPStatusError(
                "", request=httpx.Request("GET", "https://t.me"), response=httpx.Response(401)
            ),
            False,
        ),
        (ValueError("schema"), False),
    ],
)
def test_only_timeouts_429_and_5xx_are_transient(failure: Exception, retried: bool) -> None:
    assert transient_http_error(failure) is retried


def test_a_channel_without_a_web_preview_has_nothing_to_search() -> None:
    def redirect(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"Location": "https://t.me/ovdinfolive"})

    assert (
        asyncio.run(search_over(httpx.MockTransport(redirect)).search(SourceSearchQuery("x"))) == []
    )


def test_only_channels_of_the_registry_can_be_configured() -> None:
    registry = [CHANNEL]

    assert configured_telegram_channels(
        {"INVESTIGATION_TELEGRAM_CHANNELS": "ovdinfolive"}, registry=registry
    ) == [CHANNEL]
    assert (
        configured_telegram_channels({"INVESTIGATION_TELEGRAM_CHANNELS": "none"}, registry=registry)
        == []
    )
    with pytest.raises(SearchConfigurationError, match="evil_channel"):
        configured_telegram_channels(
            {"INVESTIGATION_TELEGRAM_CHANNELS": "evil_channel"}, registry=registry
        )
    with pytest.raises(SearchConfigurationError):
        TelegramChannelSearch(TelegramChannel(username="a/../b", title="", topics=()))


def test_a_post_found_outside_is_external_evidence_and_a_lead_only() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=page(("ovdinfolive", 7, "Сидорову предъявили обвинение по статье 280 УК РФ")),
        )

    retriever = SourceSearchRetriever(search_over(httpx.MockTransport(handler)), source="telegram")

    (item,) = asyncio.run(
        retriever.search(
            InvestigationQuery(kind="keywords", name="Сидоров", terms=("статья",), fact="charges")
        )
    )

    assert retriever.name == "telegram:tg-ovdinfolive"
    assert retriever.capabilities.network and not retriever.capabilities.local
    assert (item.source_type, item.fact, item.leads) == ("external", None, ("charges",))
    assert item.retrieved_at is not None and item.external_id == "7"


def test_a_post_that_names_the_person_but_not_the_words_looked_for_is_no_lead() -> None:
    """A post that only names them ends nobody's search for the articles."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=page(("ovdinfolive", 7, "Сидоров дал интервью в прямом эфире"))
        )

    retriever = SourceSearchRetriever(search_over(httpx.MockTransport(handler)), source="telegram")

    (item,) = asyncio.run(
        retriever.search(
            InvestigationQuery(kind="keywords", name="Сидоров", terms=("статья",), fact="charges")
        )
    )

    assert item.leads == ()
    # The words of the request still go to the channel's own search, which is loose.
    assert item.text == "Сидоров дал интервью в прямом эфире"


@pytest.mark.skipif(not os.environ.get("INVESTIGATION_LIVE_SEARCH"), reason="live network: opt-in")
def test_live_telegram_search_answers() -> None:
    found = asyncio.run(TelegramChannelSearch(CHANNEL).search(SourceSearchQuery("суд", limit=3)))

    assert found and all(item.url.startswith("https://t.me/ovdinfolive/") for item in found)


def test_a_post_that_does_not_name_the_person_is_dropped() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=page(
                ("ovdinfolive", 3, "Олегу Куваеву заочно запросили 5 лет"),
                ("ovdinfolive", 2, "Минюст пополнил реестр иноагентов"),
                ("ovdinfolive", 1, "Создателя «Масяни» Олега Куваева оштрафовали"),
            ),
        )

    retriever = SourceSearchRetriever(search_over(httpx.MockTransport(handler)), source="telegram")

    found = asyncio.run(retriever.search(InvestigationQuery(kind="keywords", name="Олег Куваев")))

    assert [item.external_id for item in found] == ["3", "1"]


def test_the_words_of_a_search_never_reach_the_http_client_s_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """httpx logs each request with its address; the telemetry's hash and length are
    worth nothing if the name stands a line above them."""
    marker = "Засекреченный Маркер"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=page(("ovdinfolive", 1, marker)))

    with caplog.at_level(logging.DEBUG):
        asyncio.run(search_over(httpx.MockTransport(handler)).search(SourceSearchQuery(marker)))

    logged = "\n".join(record.getMessage() for record in caplog.records)
    # The request was logged — by its channel alone.
    assert "https://t.me/s/ovdinfolive" in logged
    for form in (marker, quote(marker), quote_plus(marker), "Засекреченный", "q="):
        assert form not in logged


def test_an_address_that_is_no_search_is_logged_whole(caplog: pytest.LogCaptureFixture) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"ok")

    async def ask() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await client.get("https://t.me/s/ovdinfolive", params={"before": "5"})
            await client.get("https://example.test/search", params={"q": "слово"})

    with caplog.at_level(logging.INFO, logger="httpx"):
        asyncio.run(ask())

    logged = "\n".join(record.getMessage() for record in caplog.records)
    # Only a query on a channel's page is cut; nothing else of the process's logging is.
    assert "https://example.test/search?q=" in logged


def test_an_answer_that_is_not_a_page_is_a_failed_search_not_an_empty_one() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"error": "rate limited"})

    with pytest.raises(SearchAnswerError):
        asyncio.run(search_over(httpx.MockTransport(handler)).search(SourceSearchQuery("x")))
