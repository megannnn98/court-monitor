"""The web as one more configured source of the read-only search: Tavily.

A commercial search API stands in for what a project's own channels do not cover —
the open web, read as its results. What keeps it the same kind of object as
`TelegramChannelSearch`: one address (the API's own endpoint, a constant), the words
travel only in the body, nothing is stored, extracted or grouped, and the full text
comes from the answer itself rather than from a fetch this code makes. That last part
is why there is no page loader here: the backend only ever talks to `api.tavily.com`,
so there is no URL for a found text to steer it at (ADR 0027). What arrives is Markdown,
and not every result carries a full text at all; where it does not, the snippet is what
is kept.

The key (`TAVILY_API_KEY`) lives in the object and nowhere else: not in `repr`, not in
an exception, not in a log line, not in a stored run. The words asked are never logged
either — only the source and how many results came back.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from urllib.parse import unquote

import httpx

from ingestion.search import (
    DEFAULT_ATTEMPTS,
    DEFAULT_BASE_DELAY_SECONDS,
    DEFAULT_TIMEOUT_SECONDS,
    MAX_QUERY_LENGTH,
    MAX_TEXT,
    ExternalEvidence,
    SearchAnswerError,
    SourceSearchQuery,
)
from monitor_core.retry import retry_async, transient_http_error

logger = logging.getLogger("sources")

# The one address this search ever asks. A constant, never built from a found text.
TAVILY_URL = "https://api.tavily.com/search"
TAVILY_API_KEY_ENV = "TAVILY_API_KEY"
# Tavily answers 0–20 results; a search asks for what its own retriever wanted.
MAX_RESULTS = 10
# «basic» is one credit; «advanced» is two and buys a slower, longer crawl.
SEARCH_DEPTH = "basic"
# How much of the text before the words stand is kept, so the excerpt opens at a
# sentence rather than mid-word. The retriever filters on the name, so it must be
# inside the excerpt, not at its very first character.
EXCERPT_LEAD = 200
# How far after the anchor the asked words still count as standing together: what a
# reader of the evidence is shown around the place (the retriever's model sees 600).
NEAR = 600
_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)
# Letters a declined word of a name may differ in at its end.
_ENDING = 3
_MIN_STEM = 3


class TavilySearch:
    """Tavily's search API, asked by words: one more `SourceSearch` (ADR 0027)."""

    def __init__(
        self,
        api_key: str,
        *,
        client: httpx.AsyncClient | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        attempts: int = DEFAULT_ATTEMPTS,
        base_delay_seconds: float = DEFAULT_BASE_DELAY_SECONDS,
    ) -> None:
        if not api_key.strip():
            raise ValueError("a Tavily search needs an API key")
        if attempts < 1:
            raise ValueError("attempts must be at least 1")
        self._api_key = api_key
        # None: a client per search, closed with it — a search runs on whatever event
        # loop its caller has. A caller's own client is the caller's to close.
        self._client = client
        self.timeout = httpx.Timeout(timeout_seconds)
        self._attempts = attempts
        self._base_delay_seconds = base_delay_seconds

    def __repr__(self) -> str:
        # The key is never part of what this object looks like in a log or a traceback.
        return f"{type(self).__name__}()"

    @property
    def source(self) -> str:
        return "tavily"

    @property
    def title(self) -> str:
        return "Tavily"

    def request_body(
        self,
        text: str,
        *,
        limit: int,
        published_from: datetime | None = None,
        published_to: datetime | None = None,
    ) -> dict[str, object]:
        """What is asked of Tavily: the words in the body, the address a constant."""
        # A query that keeps to a window of dates is asked within it: days, as Tavily
        # reads them.
        window = {
            key: day.date().isoformat()
            for key, day in (("start_date", published_from), ("end_date", published_to))
            if day is not None
        }
        return {
            **window,
            "query": text,
            "max_results": max(0, min(limit, MAX_RESULTS)),
            "search_depth": SEARCH_DEPTH,
            # The page's own text, so no second fetch of what the answer already read.
            # `true`, not the «text» the documentation lists beside «markdown»: the
            # string answers an empty raw_content, and an empty one costs the retriever
            # most of its results — a snippet does not name the person.
            "include_raw_content": True,
            "include_published_date": True,
            # Free, and it says what this search cost in the answer itself.
            "include_usage": True,
            # The answer is a summary written by a model: this search reads texts.
            "include_answer": False,
        }

    async def search(self, query: SourceSearchQuery) -> list[ExternalEvidence]:
        text = " ".join(query.text.split())[:MAX_QUERY_LENGTH]
        if not text:
            return []

        async def ask(client: httpx.AsyncClient) -> httpx.Response:
            response = await client.post(
                TAVILY_URL,
                json=self.request_body(
                    text,
                    limit=query.limit,
                    published_from=query.published_from,
                    published_to=query.published_to,
                ),
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "User-Agent": "court-monitor-investigation/1.0",
                },
                timeout=self.timeout,
            )
            response.raise_for_status()
            return response

        async def with_retry(client: httpx.AsyncClient) -> httpx.Response:
            return await retry_async(
                lambda: ask(client),
                attempts=self._attempts,
                should_retry=transient_http_error,
                delay_seconds=lambda failures: self._base_delay_seconds * (2 ** (failures - 1)),
                sleep=asyncio.sleep,
            )

        if self._client is not None:
            response = await with_retry(self._client)
        else:
            async with httpx.AsyncClient() as client:
                response = await with_retry(client)
        found = self.parse(response, text=text, limit=query.limit)
        # Never the words: a person's name does not stand in the process log.
        logger.info(
            "event=source_search source=%s results=%d%s",
            self.source,
            len(found),
            _credits(response),
        )
        return found

    def parse(self, response: httpx.Response, *, text: str, limit: int) -> list[ExternalEvidence]:
        """The results as Tavily shows them: each with the site's own text, opened at the
        words that were asked for."""
        try:
            body = response.json()
        except ValueError as exc:
            # Not the answer of a search API: a failed search, not an empty one.
            raise SearchAnswerError(
                f"{self.source}: not an answer of a search API "
                f"({response.headers.get('content-type', '')[:40] or 'no content type'})"
            ) from exc
        if not isinstance(body, dict) or not isinstance(body.get("results"), list):
            raise SearchAnswerError(f"{self.source}: the answer holds no list of results")
        found: list[ExternalEvidence] = []
        for entry in body["results"]:
            if not isinstance(entry, dict):
                continue
            item = self._evidence(entry, text=text)
            if item is not None:
                found.append(item)
        return found[:limit]

    def _evidence(self, entry: Mapping[str, object], *, text: str) -> ExternalEvidence | None:
        url = str(entry.get("url") or "").strip()
        if not url.startswith(("http://", "https://")):
            return None
        body = _excerpt(str(entry.get("raw_content") or ""), text)
        if not body:
            body = _excerpt(str(entry.get("content") or ""), text)
        if not body:
            return None
        return ExternalEvidence(
            source=self.source,
            source_name=httpx.URL(url).host or self.title,
            external_id=_canonical(url),
            url=url,
            title=(str(entry["title"]).strip() or None) if entry.get("title") else None,
            text=body,
            published_at=_published_at(entry.get("published_date")),
        )


def _canonical(url: str) -> str:
    """The address as one site writes it alike: the id the same page has everywhere."""
    value = url.split("#", 1)[0].split("?", 1)[0].lower()
    value = value.removeprefix("https://").removeprefix("http://").removeprefix("www.")
    return value.rstrip("/")


def _credits(response: httpx.Response) -> str:
    """What the search cost, as it is written in its own log line: Tavily says so in
    the answer when asked (`include_usage`), free. Nothing when it does not."""
    try:
        body = response.json()
        credits = body.get("usage", {}).get("credits") if isinstance(body, dict) else None
    except ValueError:
        return ""
    return f" credits={credits}" if isinstance(credits, (int, float)) else ""


# The full text comes as Markdown. A menu of links is a chain of addresses, and those
# are what the 600 characters a reader is shown would be spent on.
_MARKDOWN_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
# A link whose address holds parentheses of its own («Функция (математика)»): the simple
# form stops at the first `)` and leaves the tail of the address behind as `")`.
_MARKDOWN_LINK = re.compile(r"\[([^\]]*)\]\(((?:[^()]|\([^()]*\))*)\)")
# A footnote of a wiki page — a number and an anchor, which say nothing to a reader.
_MARKDOWN_FOOTNOTE = re.compile(r"\[\[\d+\]\]\([^)]*\)|\[\d+\]\([^)]*\)|\[[^\]]{0,40}\]\(#[^)]*\)")
_MARKDOWN_BARE_URL = re.compile(r"<https?://[^>]+>|https?://\S+")
# A heading's hashes and a quote's marker, at the start of their own line only.
_MARKDOWN_MARKS = re.compile(r"(?m)^\s{0,3}#{1,6}\s+|^\s{0,3}>\s?")
# What is left of a page's emphasis: a name in bold («**Новая газета**») and a bullet
# («* Википедия»). Only the marks a reader would not read as words.
_MARKDOWN_EMPHASIS = re.compile(r"\*\*|(?<=\s)\*(?=\s)|\*(?=\S)|(?<=\S)\*(?=\s)")
# A word an address cut in half: «%D0%B8%D0%BA%D0%B8» inside a word is not a word.
_PERCENT_ESCAPE = re.compile(r"(?:%[0-9A-Fa-f]{2}){2,}")


def _plain(text: str) -> str:
    """The page's own words without its markup: an image is dropped whole, a link keeps
    what it says and loses where it goes, a footnote and a bare address are dropped, a
    heading loses its hashes, a word left in bold loses its marks, and a word an address
    cut in half is put back together."""
    without_images = _MARKDOWN_IMAGE.sub(" ", text)
    without_links = _MARKDOWN_LINK.sub(r"\1", without_images)
    without_notes = _MARKDOWN_FOOTNOTE.sub(" ", without_links)
    without_urls = _MARKDOWN_BARE_URL.sub(" ", without_notes)
    without_marks = _MARKDOWN_MARKS.sub(" ", without_urls)
    without_emphasis = _MARKDOWN_EMPHASIS.sub(" ", without_marks)
    return " ".join(unquote(_PERCENT_ESCAPE.sub("%", without_emphasis)).split())


def _excerpt(text: str, words: str) -> str:
    """The window of the page where the most of the asked words stand together.

    A page's own header holds one of them long before the article does — a menu of
    «Статьи», another «Мария» in a sidebar — and an excerpt the retriever cannot find the
    name in is a result thrown away whole. So every place a word begins a word is weighed
    by how many of the asked words the window around it holds, and the best such window
    is what is kept: a name in any case of the noun, near the rest of the words asked.
    The markup is taken off first: a menu of Markdown links is a chain of addresses, and
    the words of a link are the words of the site, not of the article.
    """
    body = _plain(text)
    if not body:
        return ""
    folded = body.lower().replace("ё", "е")
    stems = _stems(words)
    places = [at for stem in stems for at in _places(folded, stem)]
    if not places:
        return body[:MAX_TEXT]

    def held(place: int) -> tuple[int, int, int]:
        # What stands close to the place, in the characters a reader is actually shown:
        # the words asked for together, not merely somewhere in the same 2000. Of the
        # places that hold them all, the one where they end soonest — a window whose
        # words run to its far end is a window whose text begins long before them.
        around = folded[max(0, place - EXCERPT_LEAD) : place + NEAR]
        last: dict[str, int] = {}
        for stem in stems:
            stands = list(_places(around, stem))
            if stands:
                last[stem] = stands[-1]
        return (len(last), -(max(last.values()) if last else 0), -place)

    start = max(0, max(places, key=held) - EXCERPT_LEAD)
    return body[start : start + MAX_TEXT]


def _stems(text: str) -> list[str]:
    """The asked words by their beginnings, each one once: «статья» stands in «статье»
    and «Мария» in «Марию». The same rule the retriever filters a result by
    (`investigation.services.external.names`), repeated here rather than imported — a
    domain module never reaches into the investigation, and the other way round."""
    return list(
        dict.fromkeys(
            word[: max(_MIN_STEM, len(word) - _ENDING)]
            for word in _WORD.findall(text.lower().replace("ё", "е"))
            if len(word) > 1
        )
    )


def _places(folded: str, stem: str) -> Iterator[int]:
    """Where the stem begins a word of the text: the same letters inside another word
    are not it."""
    return (match.start() for match in re.finditer(rf"(?<![а-яa-z]){re.escape(stem)}", folded))


def _published_at(value: object) -> datetime | None:
    """When the page was published, as Tavily writes it: RFC 2822
    («Tue, 21 Jul 2026 00:00:00 GMT»), not the ISO the tests were written with."""
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    try:
        published = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        try:
            published = datetime.fromisoformat(raw)
        except ValueError:
            return None
    # A date without a time is read as UTC: every other source gives an instant.
    return published.replace(tzinfo=UTC) if published.tzinfo is None else published


def tavily_search_from_env(
    env: Mapping[str, str] | None = None, *, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
) -> TavilySearch | None:
    """The web search of the run, if a key is configured; None without `TAVILY_API_KEY`."""
    env = os.environ if env is None else env
    api_key = (env.get(TAVILY_API_KEY_ENV) or "").strip()
    if not api_key:
        return None
    return TavilySearch(api_key, timeout_seconds=timeout_seconds)
