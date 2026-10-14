"""The web as one more source of the read-only search: Tavily over its own remote MCP.

No site is asked and no key is needed: the server is an in-process MCP server built by
the official SDK and reached by the official SDK's client (`tests/sources/mcp_server.py`),
so the handshake, the tool listing, the tool call and the answer's two shapes are the
real ones and only the network between the ends is gone.

What is held: the one endpoint with the key as a bearer header and the words only in the
body of a tool call; the tool and the arguments come from the schema the server publishes
rather than from what this code remembers; the page's own text opens at the words, so a
name is inside the excerpt; a result without an address or text is nothing; a timeout and
a dropped connection are tried again, a rejected key and an answer that is not a search's
are not; the key and the words stand in no log line, no `repr` and no exception. The live
check is opt-in: `INVESTIGATION_LIVE_SEARCH=1` with `TAVILY_API_KEY`.
"""

from __future__ import annotations

import asyncio
import gc
import logging
import os
from datetime import UTC, datetime
from typing import Any

import pytest
from sources.mcp_server import ENDPOINT, KEY, fake_tavily_mcp, page, results, served_tavily_mcp

from ingestion.search import SearchAnswerError, SourceSearchQuery
from ingestion.web_search import (
    TAVILY_API_KEY_ENV,
    TAVILY_MCP_URL,
    TavilyMcpError,
    TavilyMcpSearch,
    _AskedWordsOutOfLogs,
    tavily_search_from_env,
)
from investigation.services.external import names

# Read when the module is loaded: the fixture that keeps every test off the network takes
# the key out of the environment before a test runs, the live one included.
LIVE_KEY = (
    os.environ.get(TAVILY_API_KEY_ENV) if os.environ.get("INVESTIGATION_LIVE_SEARCH") else None
)
NAME = "Мария Бонцлер"
WORDS = f"{NAME} статья"


def search_over(client: Any) -> TavilyMcpSearch:
    return TavilyMcpSearch(KEY, url=ENDPOINT, client=client, base_delay_seconds=0)


async def ask(client: Any, *answers: Any, **options: Any) -> list[Any]:
    """One search against the fake server, inside the server's own lifetime."""
    async with fake_tavily_mcp(list(answers) or [results()], **options) as (held, _recorder):
        return await search_over(held).search(SourceSearchQuery(WORDS, limit=5))


def run_search(client: Any, *answers: Any, **options: Any) -> list[Any]:
    return asyncio.run(ask(client, *answers, **options))


# ── the transport ───────────────────────────────────────────────────────────────


def test_the_search_asks_the_one_mcp_endpoint_with_the_key_as_a_bearer_header() -> None:
    async def go() -> tuple[Any, list[dict[str, Any]]]:
        async with fake_tavily_mcp([results(page())]) as (client, recorder):
            found = await search_over(client).search(SourceSearchQuery(WORDS, limit=5))
            return found, recorder.calls

    found, calls = asyncio.run(go())

    assert TAVILY_MCP_URL == "https://mcp.tavily.com/mcp"
    assert len(found) == 1
    # The key is a header, never an address, and the words are in the arguments.
    assert calls == [
        {
            "query": WORDS,
            "max_results": 5,
            "search_depth": "basic",
            "include_raw_content": True,
            "start_date": "",
            "end_date": "",
        }
    ]


def test_the_key_is_sent_as_a_bearer_header_and_the_words_never_become_an_address() -> None:
    async def go() -> list[str | None]:
        async with fake_tavily_mcp([results(page())]) as (client, recorder):
            await search_over(client).search(SourceSearchQuery(WORDS, limit=5))
            return recorder.authorizations

    assert set(asyncio.run(go())) == {f"Bearer {KEY}"}


def test_the_tool_is_the_one_the_server_publishes_and_no_other_is_asked() -> None:
    async def go() -> list[str]:
        async with fake_tavily_mcp([results(page())]) as (client, _recorder):
            published = await search_over(client).tools()
        return [str(tool["name"]) for tool in published]

    assert asyncio.run(go()) == ["tavily_search"]


def test_a_server_that_publishes_no_search_tool_is_a_failed_search_and_says_what_there_is() -> None:
    with pytest.raises(SearchAnswerError) as failure:
        run_search(None, results(page()), publish_tool=False)

    assert "tavily_search" in str(failure.value)


def test_a_server_that_answers_with_a_different_tool_is_refused() -> None:
    async def go() -> list[Any]:
        async with fake_tavily_mcp([results(page())], tool="tavily_crawl") as (client, _r):
            return await TavilyMcpSearch(
                KEY, url=ENDPOINT, client=client, base_delay_seconds=0
            ).search(SourceSearchQuery(WORDS, limit=5))

    with pytest.raises(SearchAnswerError, match="tavily_search"):
        asyncio.run(go())


# ── the arguments, read off the published schema ─────────────────────────────────


def test_an_argument_the_server_does_not_publish_is_not_sent() -> None:
    wanted = {"query": "x", "max_results": 5, "include_usage": True, "topic": "general"}

    assert TavilyMcpSearch.tool_arguments(
        {"properties": {"query": {}, "max_results": {}}, "additionalProperties": False}, wanted
    ) == {"query": "x", "max_results": 5}
    # A schema that says nothing about its properties takes what it is given.
    assert TavilyMcpSearch.tool_arguments({}, wanted) == wanted


def test_a_window_of_dates_is_asked_of_tavily_as_its_first_and_last_day() -> None:
    async def go() -> list[dict[str, Any]]:
        async with fake_tavily_mcp([results(page()), results(page())]) as (client, recorder):
            search = search_over(client)
            await search.search(
                SourceSearchQuery(
                    WORDS,
                    limit=5,
                    published_from=datetime(2026, 8, 6, 9, 30, tzinfo=UTC),
                    published_to=datetime(2026, 11, 4, 9, 30, tzinfo=UTC),
                )
            )
            await search.search(
                SourceSearchQuery(WORDS, published_from=datetime(2026, 8, 6, tzinfo=UTC))
            )
            return recorder.calls

    bounded, open_ended = asyncio.run(go())
    assert (bounded["start_date"], bounded["end_date"]) == ("2026-08-06", "2026-11-04")
    assert open_ended["start_date"] == "2026-08-06" and open_ended["end_date"] == ""


def test_the_asked_limit_is_asked_for_and_no_more_than_tavily_answers_is_kept() -> None:
    async def go() -> tuple[int, list[dict[str, Any]]]:
        pages = [page(url=f"https://news.example.test/{number}") for number in range(10)]
        async with fake_tavily_mcp([results(*pages)]) as (client, recorder):
            found = await search_over(client).search(SourceSearchQuery("x", limit=3))
            return len(found), recorder.calls

    kept, calls = asyncio.run(go())
    assert kept == 3
    assert calls[0]["max_results"] == 3

    async def capped() -> list[dict[str, Any]]:
        async with fake_tavily_mcp([results()]) as (client, recorder):
            await search_over(client).search(SourceSearchQuery("x", limit=50))
            return recorder.calls

    # Tavily answers no more than twenty; the search asks for what it wants, capped.
    assert asyncio.run(capped())[0]["max_results"] == 10


def test_the_depth_of_the_search_is_a_setting_and_is_sent_as_it_is() -> None:
    async def go() -> list[dict[str, Any]]:
        async with fake_tavily_mcp([results(page())]) as (client, recorder):
            await TavilyMcpSearch(
                KEY, url=ENDPOINT, client=client, search_depth="advanced", base_delay_seconds=0
            ).search(SourceSearchQuery(WORDS, limit=5))
            return recorder.calls

    assert asyncio.run(go())[0]["search_depth"] == "advanced"
    with pytest.raises(ValueError, match="search_depth"):
        TavilyMcpSearch(KEY, search_depth="deep")


# ── what came back ───────────────────────────────────────────────────────────────


def test_the_answer_is_read_as_external_evidence_of_the_site_that_published_it() -> None:
    (item,) = run_search(None, results(page(published_date="Tue, 21 Jul 2026 00:00:00 GMT")))

    assert item.source == "tavily"
    assert item.source_name == "news.example.test"
    assert item.url == "https://news.example.test/boncler-1"
    # One page has one id however it is written.
    assert item.external_id == "news.example.test/boncler-1"
    assert item.title == "Суд над адвокатом"
    assert "Бонцлер" in item.text
    # Tavily writes a date as a mail header, not as ISO: it is read either way.
    assert item.published_at == datetime(2026, 7, 21, tzinfo=UTC)


def test_an_answer_the_server_sends_as_structured_content_and_as_a_text_block_reads_the_same() -> (
    None
):
    structured = run_search(None, results(page()))
    in_text = run_search(None, results(page()), text_only=True)

    assert [(i.url, i.text, i.title) for i in structured] == [
        (i.url, i.text, i.title) for i in in_text
    ]


@pytest.mark.parametrize(
    ("published", "read"),
    [
        ("Tue, 21 Jul 2026 00:00:00 GMT", datetime(2026, 7, 21, tzinfo=UTC)),
        ("2026-09-25T07:00:14Z", datetime(2026, 9, 25, 7, 0, 14, tzinfo=UTC)),
        ("2026-09-25", datetime(2026, 9, 25, tzinfo=UTC)),
        # The published tool asks for no date and returns none: a web page of the
        # investigation is undated evidence, and is kept as it is rather than dropped.
        (None, None),
        ("вчера", None),
        (12345, None),
    ],
)
def test_a_published_date_is_read_in_the_format_tavily_writes_it(
    published: str | None, read: datetime | None
) -> None:
    (item,) = run_search(None, results(page(published_date=published)))
    assert item.published_at == read


def test_the_text_is_the_pages_own_and_opens_at_the_words_asked_for() -> None:
    filler = "Меню. Подписка. Реклама. " * 200
    raw = f"{filler}Заголовок публикации. {NAME} обжаловала приговор. Хвост новости."

    (item,) = run_search(None, results(page(raw_content=raw)))

    assert len(item.text) < len(raw)
    assert NAME in item.text
    # The retriever shows 600 characters: the name must be inside them, not behind the
    # site's own boilerplate.
    assert NAME in item.text[:600]
    assert item.text.endswith("Хвост новости.")


def test_the_excerpt_is_anchored_where_the_asked_words_stand_together() -> None:
    """A header holds the words before the article does: a menu of «Статьи», another
    «Мария» in a sidebar, and a namesake the run is not asking about."""
    header = (
        "Главная Статьи Интервью Мария Иванова — автор блога о праве. Статья. Статьи. Мария. " * 40
    )
    between = "Подвал: подписка, реклама, счётчики. " * 30
    raw = (
        f"{header}{between}Суд над адвокатом. "
        "Марию Бонцлер оставили под стражей по статье 280. " + "Подробности дела. " * 300
    )

    (item,) = run_search(None, results(page(raw_content=raw)))

    # Declined («Марию», «по статье») and thousands of characters further down: the name
    # is still inside what the retriever filters by and what the model reads of it.
    assert names(item.text, NAME)
    assert names(item.text[:600], NAME)
    assert "по статье 280" in item.text


def test_the_markup_of_a_page_is_taken_off_before_the_excerpt_is_chosen() -> None:
    """The full text arrives as Markdown: a menu is a chain of addresses, and those are
    what the 600 characters a reader is shown would otherwise be spent on."""
    menu = (
        "# Главная\n\n"
        + " ".join(
            f"[{label}](https://news.example.test/section/{number})"
            for number, label in enumerate(["Статьи", "О Ведомостях", "Интервью", "Мария"] * 5)
        )
        + "\n\n![Логотип издания](https://news.example.test/logo.png)\n\n"
        + "Подкасты: <https://news.example.test/rss> и https://news.example.test/archive\n\n"
    )
    raw = (
        f"{menu}## Материал\n\nМарию Бонцлер оставили под стражей по статье 280 УК РФ. "
        + "Подробности дела. " * 300
    )

    (item,) = run_search(None, results(page(raw_content=raw)))

    assert "http" not in item.text and "](" not in item.text
    assert "#" not in item.text and "Логотип издания" not in item.text
    assert names(item.text, NAME)
    assert names(item.text[:600], NAME)
    assert "по статье 280" in item.text


def test_what_a_page_left_of_its_markup_is_taken_off() -> None:
    """Each rule here answers a page Tavily really returned: a name in bold, a bullet, a
    wiki footnote, a link whose address holds parentheses, a word an address cut in half."""
    raw = (
        "# Материал\n\n"
        "**Новая газета** Стать соучастником\n\n"
        "* Википедия:Биографии современников * Википедия:Статьи без изображений\n\n"
        "[Функция (математика)](https://ru.wikipedia.org/wiki/Функция_(математика))\n\n"
        "Они предъявляли обвинение[[1]](#cite_note-:9-1) и [12](#cite_note-12).\n\n"
        "И%D0%BA%D0%B8%D0%BF%D0%B5%D0%B4%D0%B8%D1%8F: известный российский юрист"
    )

    (item,) = run_search(None, results(page(raw_content=raw)))

    text = item.text
    assert "**" not in text and "cite_note" not in text and "%D0" not in text
    assert '")' not in text
    assert "Новая газета" in text and "Функция (математика)" in text
    assert "Википедия" in text


def test_a_word_asked_for_is_found_where_it_stands_in_another_case_of_the_noun() -> None:
    raw = "Ничего общего. " * 400 + "Мария Бонцлер: по части 3 статьи 280 УК РФ." + " Хвост. " * 300

    (item,) = run_search(None, results(page(raw_content=raw)))

    assert names(item.text, NAME)
    assert names(item.text[:600], NAME)
    assert "части 3 статьи 280" in item.text


def test_without_the_full_text_the_snippet_is_taken() -> None:
    (item,) = run_search(None, results(page(raw_content=None, content="Мария Бонцлер арестована.")))

    assert item.text == "Мария Бонцлер арестована."


def test_an_empty_answer_is_no_evidence() -> None:
    assert run_search(None, results()) == []


def test_words_nobody_typed_ask_nothing() -> None:
    async def go() -> list[Any]:
        async with fake_tavily_mcp([results(page())]) as (client, recorder):
            found = await search_over(client).search(SourceSearchQuery("  "))
            return [found, recorder.calls]

    found, calls = asyncio.run(go())
    assert found == []
    assert calls == []


@pytest.mark.parametrize("result", [{"url": "not-an-address", "content": "Мария Бонцлер"}])
def test_a_result_without_an_address_is_dropped(result: dict[str, object]) -> None:
    assert run_search(None, results(result)) == []


@pytest.mark.parametrize(
    "result",
    [{"url": "https://news.example.test/a"}, {"url": "https://n.test/a", "content": "   "}],
)
def test_a_result_without_text_is_dropped(result: dict[str, object]) -> None:
    assert run_search(None, results(result)) == []


# ── what the server answers that is not a search ─────────────────────────────────


@pytest.mark.parametrize(
    "failure",
    [
        {"nope": 1},
        {"results": "none"},
        "not json at all",
        {"result": "not a search"},
    ],
)
def test_an_answer_that_is_not_a_search_answer_is_a_failed_search(failure: Any) -> None:
    with pytest.raises(SearchAnswerError):
        run_search(None, failure)


def test_a_tool_that_fails_itself_is_a_failed_search_and_is_not_asked_again() -> None:
    async def go() -> list[dict[str, Any]]:
        async with fake_tavily_mcp([RuntimeError("upstream is down")]) as (client, recorder):
            with pytest.raises(SearchAnswerError):
                await search_over(client).search(SourceSearchQuery(WORDS, limit=5))
            return recorder.calls

    assert len(asyncio.run(go())) == 1


def test_a_rejected_key_is_not_asked_again_and_never_reaches_the_message() -> None:
    async def go() -> list[str | None]:
        async with fake_tavily_mcp([results(page())], key="a-key-the-server-does-not-know") as (
            client,
            recorder,
        ):
            with pytest.raises(TavilyMcpError) as failure:
                await search_over(client).search(SourceSearchQuery(WORDS, limit=5))
            assert KEY not in str(failure.value)
            return recorder.authorizations

    # One attempt: an authorization the server will not grant answers the same way again.
    assert len(asyncio.run(go())) == 1


@pytest.mark.parametrize("status", [429, 500, 503])
def test_a_rate_limit_and_a_server_failure_are_asked_again_once(status: int) -> None:
    """The SDK folds every HTTP failure of the server into one protocol error and keeps
    the status to itself. The status is what tells a rate limit from a refused key, so it
    is read off the HTTP client this code makes, and a server that fails once must be
    asked again. The search here builds that client itself, as one against Tavily does."""

    with served_tavily_mcp([results(page())], statuses=[status]) as (endpoint, recorder):
        found = asyncio.run(
            TavilyMcpSearch(KEY, url=endpoint, base_delay_seconds=0).search(
                SourceSearchQuery(WORDS, limit=5)
            )
        )

    assert len(found) == 1
    # A failed attempt, then a whole one: the handshake, the tool call and the retry that
    # followed the failure the server put in front of the first one.
    assert recorder.calls and recorder.posts > len(recorder.calls)


@pytest.mark.parametrize("status", [401, 403])
def test_a_refused_key_is_not_asked_again_though_the_sdk_hides_the_status(status: int) -> None:
    with (
        served_tavily_mcp([results(page())], statuses=[status]) as (endpoint, recorder),
        pytest.raises((TavilyMcpError, SearchAnswerError)),
    ):
        asyncio.run(
            TavilyMcpSearch(KEY, url=endpoint, base_delay_seconds=0).search(
                SourceSearchQuery(WORDS, limit=5)
            )
        )

    assert recorder.posts == 1


def test_a_status_nobody_recorded_is_not_taken_for_a_transient_failure() -> None:
    """A client of the caller's own carries no hook, so nothing is known of the status:
    the same call may have been refused for want of a key, and a second one would be."""

    async def go() -> int:
        async with fake_tavily_mcp([results(page())], statuses=[503]) as (client, recorder):
            with pytest.raises((TavilyMcpError, SearchAnswerError)):
                await search_over(client).search(SourceSearchQuery(WORDS, limit=5))
            return recorder.posts

    assert asyncio.run(go()) == 1


def test_a_search_that_never_answers_is_a_timeout_of_the_run_not_a_lost_run() -> None:
    async def go() -> str:
        async with fake_tavily_mcp([results(page())], delay_seconds=0.2) as (client, _recorder):
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(
                    search_over(client).search(SourceSearchQuery(WORDS, limit=5)), timeout=0.05
                )
            return "stopped"

    assert asyncio.run(go()) == "stopped"


def test_a_cancelled_search_leaves_nothing_behind() -> None:
    """A search the run stopped waiting for is cancelled mid-call. The session must end
    with the cancellation — the server's session terminated, the SDK's task groups closed
    in the task that opened them — and nothing of the client may be left to be collected
    later, in whatever task that might be."""

    async def go() -> tuple[bool, int]:
        async with fake_tavily_mcp([results(page())], delay_seconds=0.3) as (client, recorder):
            before = set(asyncio.all_tasks())
            work = asyncio.ensure_future(search_over(client).search(SourceSearchQuery(WORDS)))
            await asyncio.sleep(0.05)
            work.cancel()
            try:
                await work
            except asyncio.CancelledError:
                pass
            else:
                return False, recorder.terminations
            gc.collect()
            left = [
                task
                for task in asyncio.all_tasks() - before
                # The test server's own SSE writer is the harness, not the client.
                if "sse_starlette" not in str(task.get_coro().cr_frame.f_globals.get("__name__"))
            ]
            return not left, recorder.terminations

    clean, terminations = asyncio.run(go())
    assert clean is True
    assert terminations == 1


def test_a_search_against_a_loopback_server_answers_over_the_product_s_path() -> None:
    with served_tavily_mcp([results(page())]) as (endpoint, recorder):
        found = asyncio.run(
            TavilyMcpSearch(KEY, url=endpoint, base_delay_seconds=0).search(
                SourceSearchQuery(WORDS, limit=5)
            )
        )

    assert len(found) == 1
    assert recorder.calls == [
        {
            "query": WORDS,
            "max_results": 5,
            "search_depth": "basic",
            "include_raw_content": True,
            "start_date": "",
            "end_date": "",
        }
    ]


def test_two_searches_at_once_each_get_their_own_answer() -> None:
    """Several network searches of one run are in flight at the same time; each opens its
    own session, neither waits for the other, and neither sees the other's words."""

    async def go() -> list[list[Any]]:
        async def answers(arguments: dict[str, Any]) -> dict[str, Any]:
            await asyncio.sleep(0.05)
            return results(page(url=f"https://{arguments['query'][:4]}.test/1"))

        async with fake_tavily_mcp(answers) as (client, _recorder):
            first, second = search_over(client), search_over(client)
            return list(
                await asyncio.gather(
                    first.search(SourceSearchQuery("Мария Бонцлер статья", limit=5)),
                    second.search(SourceSearchQuery("Иван Петров обвинение", limit=5)),
                )
            )

    found = asyncio.run(go())
    assert [item.url for group in found for item in group] == [
        "https://Мари.test/1",
        "https://Иван.test/1",
    ]


def test_the_key_stands_in_no_exception_and_no_repr() -> None:
    search = TavilyMcpSearch(KEY)
    assert KEY not in repr(search)
    assert KEY not in f"{search}"
    assert KEY not in str(search)


def test_the_words_and_the_key_never_reach_the_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The MCP client logs every call's arguments at DEBUG, and an argument of a search
    is the words a person was looked up by. Even with everything at DEBUG — the transport
    included — the query and the key stand in nothing."""

    async def go() -> None:
        async with fake_tavily_mcp(
            [
                results(
                    page(
                        content="Кратко: под стражей.",
                        raw_content="Городской суд оставил под стражей.",
                    )
                )
            ]
        ) as (client, _r):
            await search_over(client).search(SourceSearchQuery(WORDS, limit=5))

    with caplog.at_level(logging.DEBUG):
        asyncio.run(go())

    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert "event=source_search source=tavily results=1" in logged
    assert "request_id=req-1" in logged
    # The SDK's own call line is not there at all: its level was raised, so a message it
    # writes about a call — arguments, answer and all — is never built.
    assert "tools/call" not in logged
    for form in (KEY, WORDS, NAME, "Бонцлер"):
        assert form not in logged


def test_the_filter_takes_the_words_out_even_if_a_call_line_is_written() -> None:
    """The level is the first line of defence; the filter is the second, and it is what
    stands if a deployment turns the SDK's logging up."""
    record = logging.LogRecord(
        name="mcp.client.streamable_http",
        level=logging.DEBUG,
        pathname=__file__,
        lineno=1,
        msg="Sending client message: method='tools/call' params={'query': \"В'ячеслав Присяжнюк статья\"}",
        args=None,
        exc_info=None,
    )

    _AskedWordsOutOfLogs().filter(record)

    assert "Присяжнюк" not in record.msg
    assert "tools/call" in record.msg


def test_a_page_the_search_brought_back_is_not_logged_by_the_transport(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The SDK writes every message it receives at DEBUG — the whole answer, a page's own
    text and all. A page a reader of the result was never shown must not turn up in the
    process log, so the transport's own level is raised and the filter is the second
    line of defence."""

    async def go() -> None:
        async with fake_tavily_mcp([results(page(raw_content=f"{NAME} арестована."))]) as (
            client,
            _r,
        ):
            await search_over(client).search(SourceSearchQuery(WORDS, limit=5))

    with caplog.at_level(logging.DEBUG):
        asyncio.run(go())

    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert WORDS not in logged
    assert "Бонцлер" not in logged


def test_a_name_with_an_apostrophe_in_it_is_not_logged_either(
    caplog: pytest.LogCaptureFixture,
) -> None:
    name = "В'ячеслав Присяжнюк"
    words = f"{name} статья"

    async def go() -> None:
        async with fake_tavily_mcp([results(page(raw_content="Ничего о деле."))]) as (client, _r):
            await search_over(client).search(SourceSearchQuery(words, limit=5))

    with caplog.at_level(logging.DEBUG):
        asyncio.run(go())

    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert words not in logged and "Присяжнюк" not in logged


def test_the_key_is_not_in_the_headers_the_transport_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def go() -> None:
        async with fake_tavily_mcp([results(page())]) as (client, _recorder):
            await search_over(client).search(SourceSearchQuery(WORDS, limit=5))

    with caplog.at_level(logging.DEBUG):
        asyncio.run(go())

    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert KEY not in logged


def test_what_the_search_cost_is_not_logged_as_a_number_the_server_never_sent(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The published `tavily_search` has no `include_usage` and reports no credits: the
    log line says what the server really said and nothing more."""

    async def go() -> None:
        async with fake_tavily_mcp([results(page())]) as (client, _recorder):
            await search_over(client).search(SourceSearchQuery(WORDS, limit=5))

    with caplog.at_level(logging.INFO, logger="sources"):
        asyncio.run(go())

    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert "credits" not in logged
    assert "event=source_search source=tavily results=1 mcp_session=short_lived" in logged


# ── how it is configured ─────────────────────────────────────────────────────────


def test_without_a_key_there_is_no_web_search() -> None:
    assert tavily_search_from_env({}) is None
    assert tavily_search_from_env({"TAVILY_API_KEY": "  "}) is None
    with pytest.raises(ValueError, match="API key"):
        TavilyMcpSearch("   ")

    search = tavily_search_from_env({"TAVILY_API_KEY": KEY})
    assert search is not None
    assert (search.source, search.title) == ("tavily", "Tavily")
    assert KEY not in repr(search)
    assert TAVILY_API_KEY_ENV == "TAVILY_API_KEY"


def test_the_depth_of_the_search_is_read_from_the_environment() -> None:
    assert tavily_search_from_env({"TAVILY_API_KEY": KEY}).search_depth == "basic"
    deeper = tavily_search_from_env(
        {"TAVILY_API_KEY": KEY, "INVESTIGATION_WEB_SEARCH_DEPTH": "advanced"}
    )
    assert deeper is not None
    assert deeper.search_depth == "advanced"
    with pytest.raises(ValueError, match="INVESTIGATION_WEB_SEARCH_DEPTH"):
        tavily_search_from_env({"TAVILY_API_KEY": KEY, "INVESTIGATION_WEB_SEARCH_DEPTH": "deep"})


# ── the live server ──────────────────────────────────────────────────────────────


@pytest.mark.skipif(not LIVE_KEY, reason="live network with a real key: opt-in")
def test_the_official_remote_mcp_server_publishes_a_search_tool_and_answers_one() -> None:
    assert LIVE_KEY is not None
    search = TavilyMcpSearch(LIVE_KEY, timeout_seconds=60.0)

    published = asyncio.run(search.tools())
    assert "tavily_search" in {str(tool["name"]) for tool in published}

    found = asyncio.run(search.search(SourceSearchQuery(NAME, limit=3)))

    assert found
    assert all(item.url.startswith("https://") for item in found)
    assert all(item.text for item in found)
    # The key is nowhere in what the search object says about itself or about an answer.
    assert LIVE_KEY not in repr(search)
