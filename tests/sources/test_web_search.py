"""The web as one more source of the read-only search: Tavily, over a fake transport.
No site is asked.

What is held: one address, the words only in the body; the page's own text opens at the
words, so a name is inside the excerpt; a result without an address or text is nothing;
a rate limit and a server failure are tried again, a bad key and a bad query are not;
an answer that is not a search answer is a failed search; the key and the words stand in
no log line, no `repr` and no exception. The live check is opt-in:
`INVESTIGATION_LIVE_SEARCH=1` with `TAVILY_API_KEY`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os

import httpx
import pytest

from ingestion.search import SearchAnswerError, SourceSearchQuery
from ingestion.web_search import TAVILY_URL, TavilySearch, tavily_search_from_env
from investigation.services.external import names

KEY = "tvly-a-key-nobody-must-see"
NAME = "Мария Бонцлер"
WORDS = f"{NAME} статья"


def answer(*results: dict[str, object]) -> httpx.Response:
    return httpx.Response(200, json={"results": list(results)})


def page(
    *,
    title: str = "Суд над адвокатом",
    raw: str | None = "Городской суд оставил Марию Бонцлер под стражей.",
    published: str | None = "2026-09-25T07:00:14Z",
    url: str = "https://news.example.test/boncler-1",
    content: str = "Кратко: Мария Бонцлер арестована.",
) -> dict[str, object]:
    found: dict[str, object] = {"title": title, "url": url, "content": content}
    if raw is not None:
        found["raw_content"] = raw
    if published is not None:
        found["published_date"] = published
    return found


def search_over(handler: httpx.MockTransport) -> TavilySearch:
    return TavilySearch(KEY, client=httpx.AsyncClient(transport=handler), base_delay_seconds=0)


def test_a_search_asks_the_one_address_with_the_key_in_the_header_and_the_words_in_the_body() -> (
    None
):
    asked: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(request)
        return answer(page())

    asyncio.run(search_over(httpx.MockTransport(handler)).search(SourceSearchQuery(WORDS, limit=5)))

    (request,) = asked
    assert str(request.url) == TAVILY_URL == "https://api.tavily.com/search"
    assert request.method == "POST"
    assert request.headers["Authorization"] == f"Bearer {KEY}"
    body = json.loads(request.content)
    assert body == {
        "query": WORDS,
        "max_results": 5,
        "search_depth": "basic",
        "include_raw_content": "text",
        "include_published_date": True,
        "include_answer": False,
    }
    # The words never become an address: there is no query string to carry them.
    assert WORDS not in str(request.url)


def test_the_answer_is_read_as_external_evidence_of_the_site_that_published_it() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return answer(page())

    (item,) = asyncio.run(
        search_over(httpx.MockTransport(handler)).search(SourceSearchQuery(WORDS, limit=5))
    )

    assert item.source == "tavily"
    assert item.source_name == "news.example.test"
    assert item.url == "https://news.example.test/boncler-1"
    # One page has one id however it is written.
    assert item.external_id == "news.example.test/boncler-1"
    assert item.title == "Суд над адвокатом"
    assert "Бонцлер" in item.text
    assert item.published_at is not None and item.published_at.year == 2026


def test_the_text_is_the_pages_own_and_opens_at_the_words_asked_for() -> None:
    filler = "Меню. Подписка. Реклама. " * 200
    raw = f"{filler}Заголовок публикации. {NAME} обжаловала приговор. Хвост новости."

    def handler(request: httpx.Request) -> httpx.Response:
        return answer(page(raw=raw))

    (item,) = asyncio.run(
        search_over(httpx.MockTransport(handler)).search(SourceSearchQuery(WORDS, limit=5))
    )

    # The site's own header is cut: what is kept opens just before the words.
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

    def handler(request: httpx.Request) -> httpx.Response:
        return answer(page(raw=raw))

    (item,) = asyncio.run(
        search_over(httpx.MockTransport(handler)).search(SourceSearchQuery(WORDS, limit=5))
    )

    # Declined («Марию», «по статье») and thousands of characters further down: the name
    # is still inside what the retriever filters by and what the model reads of it.
    assert names(item.text, NAME)
    assert names(item.text[:600], NAME)
    assert "по статье 280" in item.text


def test_a_word_asked_for_is_found_where_it_stands_in_another_case_of_the_noun() -> None:
    raw = "Ничего общего. " * 400 + "Мария Бонцлер: по части 3 статьи 280 УК РФ." + " Хвост. " * 300

    def handler(request: httpx.Request) -> httpx.Response:
        return answer(page(raw=raw))

    (item,) = asyncio.run(
        search_over(httpx.MockTransport(handler)).search(SourceSearchQuery(WORDS, limit=5))
    )

    assert names(item.text, NAME)
    assert names(item.text[:600], NAME)
    assert "части 3 статьи 280" in item.text


def test_without_the_full_text_the_snippet_is_taken() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return answer(page(raw=None, content="Мария Бонцлер арестована по статье 280."))

    (item,) = asyncio.run(
        search_over(httpx.MockTransport(handler)).search(SourceSearchQuery(WORDS, limit=5))
    )

    assert item.text == "Мария Бонцлер арестована по статье 280."


def test_the_asked_limit_is_asked_for_and_no_more_is_kept() -> None:
    asked: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(json.loads(request.content)["max_results"])
        return answer(*[page(url=f"https://news.example.test/{number}") for number in range(10)])

    assert (
        len(
            asyncio.run(
                search_over(httpx.MockTransport(handler)).search(SourceSearchQuery("x", limit=3))
            )
        )
        == 3
    )
    # Tavily asks for no more than twenty; the search asks for what it wants.
    assert asked == [3]

    def plain(request: httpx.Request) -> httpx.Response:
        asked.append(json.loads(request.content)["max_results"])
        return answer()

    asyncio.run(search_over(httpx.MockTransport(plain)).search(SourceSearchQuery("x", limit=50)))
    assert asked[-1] == 10


def test_an_empty_answer_is_no_evidence() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return answer()

    assert (
        asyncio.run(search_over(httpx.MockTransport(handler)).search(SourceSearchQuery(WORDS)))
        == []
    )


def test_words_nobody_typed_ask_nothing() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return answer(page())

    assert (
        asyncio.run(search_over(httpx.MockTransport(handler)).search(SourceSearchQuery("  "))) == []
    )
    assert calls == 0


@pytest.mark.parametrize("result", [{"url": "not-an-address", "content": "Мария Бонцлер"}])
def test_a_result_without_an_address_is_dropped(result: dict[str, object]) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return answer(result)

    assert (
        asyncio.run(search_over(httpx.MockTransport(handler)).search(SourceSearchQuery(WORDS)))
        == []
    )


@pytest.mark.parametrize(
    "result",
    [{"url": "https://news.example.test/a"}, {"url": "https://n.test/a", "content": "   "}],
)
def test_a_result_without_text_is_dropped(result: dict[str, object]) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return answer(result)

    assert (
        asyncio.run(search_over(httpx.MockTransport(handler)).search(SourceSearchQuery(WORDS)))
        == []
    )


@pytest.mark.parametrize(
    "failure",
    [
        httpx.Response(200, text="rate limited"),
        httpx.Response(200, json={"error": "forbidden"}),
        httpx.Response(200, json={"results": "none"}),
    ],
)
def test_an_answer_that_is_not_a_search_answer_is_a_failed_search(failure: httpx.Response) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return failure

    with pytest.raises(SearchAnswerError):
        asyncio.run(search_over(httpx.MockTransport(handler)).search(SourceSearchQuery(WORDS)))


@pytest.mark.parametrize("status", [401, 432, 433, 400])
def test_a_bad_key_or_a_bad_query_fails_at_once(status: int) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(status, json={"detail": "nope"})

    with pytest.raises(httpx.HTTPStatusError) as failure:
        asyncio.run(search_over(httpx.MockTransport(handler)).search(SourceSearchQuery(WORDS)))

    assert failure.value.response.status_code == status
    assert calls == 1


@pytest.mark.parametrize("status", [429, 500, 503])
def test_a_rate_limit_and_a_server_failure_are_asked_again_once(status: int) -> None:
    statuses = iter([status, 200])

    def handler(request: httpx.Request) -> httpx.Response:
        code = next(statuses)
        return answer(page()) if code == 200 else httpx.Response(code, json={"detail": "later"})

    assert (
        len(asyncio.run(search_over(httpx.MockTransport(handler)).search(SourceSearchQuery(WORDS))))
        == 1
    )


@pytest.mark.parametrize("failure", [httpx.ReadTimeout("slow"), httpx.ConnectError("refused")])
def test_a_timeout_and_a_dropped_connection_are_asked_again_once(failure: Exception) -> None:
    attempts = iter([failure, None])

    def handler(request: httpx.Request) -> httpx.Response:
        first = next(attempts)
        if first is not None:
            raise first
        return answer(page())

    assert (
        len(asyncio.run(search_over(httpx.MockTransport(handler)).search(SourceSearchQuery(WORDS))))
        == 1
    )


def test_a_failure_that_passes_the_retry_is_raised_as_it_was() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"detail": "later"})

    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(search_over(httpx.MockTransport(handler)).search(SourceSearchQuery(WORDS)))


def test_the_key_stands_in_no_exception_and_no_repr() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"detail": "invalid key"})

    with pytest.raises(httpx.HTTPStatusError) as failure:
        asyncio.run(search_over(httpx.MockTransport(handler)).search(SourceSearchQuery(WORDS)))

    assert KEY not in str(failure.value)
    assert KEY not in repr(search_over(httpx.MockTransport(handler)))
    assert KEY not in f"{search_over(httpx.MockTransport(handler))}"


def test_the_words_and_the_key_never_reach_the_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return answer(page(raw=f"{NAME} арестована."))

    with caplog.at_level(logging.DEBUG):
        asyncio.run(search_over(httpx.MockTransport(handler)).search(SourceSearchQuery(WORDS)))

    logged = "\n".join(record.getMessage() for record in caplog.records)
    # The request itself was logged — by its address alone.
    assert TAVILY_URL in logged
    assert "event=source_search source=tavily results=1" in logged
    for form in (KEY, WORDS, NAME, "Бонцлер"):
        assert form not in logged


def test_without_a_key_there_is_no_web_search() -> None:
    assert tavily_search_from_env({}) is None
    assert tavily_search_from_env({"TAVILY_API_KEY": "  "}) is None
    with pytest.raises(ValueError, match="API key"):
        TavilySearch("   ")

    search = tavily_search_from_env({"TAVILY_API_KEY": KEY})
    assert search is not None
    assert (search.source, search.title) == ("tavily", "Tavily")
    assert KEY not in repr(search)


@pytest.mark.skipif(
    not (os.environ.get("INVESTIGATION_LIVE_SEARCH") and os.environ.get("TAVILY_API_KEY")),
    reason="live network with a real key: opt-in",
)
def test_live_tavily_search_answers() -> None:
    search = tavily_search_from_env(os.environ)
    assert search is not None

    found = asyncio.run(search.search(SourceSearchQuery(NAME, limit=3)))

    assert found and all(item.url.startswith("https://") for item in found)
