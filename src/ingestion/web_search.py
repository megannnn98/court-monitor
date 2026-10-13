"""The web as one more configured source of the read-only search: Tavily.

A commercial search API stands in for what a project's own channels do not cover —
the open web, read as its results. What keeps it the same kind of object as
`TelegramChannelSearch`: one address (the API's own endpoint, a constant), the words
travel only in the body, nothing is stored, extracted or grouped, and the full text
comes from the answer itself rather than from a fetch this code makes. That last part
is why there is no page loader here: the backend only ever talks to `api.tavily.com`,
so there is no URL for a found text to steer it at (ADR 0027).

The key (`TAVILY_API_KEY`) lives in the object and nowhere else: not in `repr`, not in
an exception, not in a log line, not in a stored run. The words asked are never logged
either — only the source and how many results came back.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from collections.abc import Mapping
from datetime import UTC, datetime

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
_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)


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

    def request_body(self, text: str, *, limit: int) -> dict[str, object]:
        """What is asked of Tavily: the words in the body, the address a constant."""
        return {
            "query": text,
            "max_results": max(0, min(limit, MAX_RESULTS)),
            "search_depth": SEARCH_DEPTH,
            # The page's own text, so no second fetch of what the answer already read.
            "include_raw_content": "text",
            "include_published_date": True,
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
                json=self.request_body(text, limit=query.limit),
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
        logger.info("event=source_search source=%s results=%d", self.source, len(found))
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


def _excerpt(text: str, words: str) -> str:
    """The text around the first place the asked words stand: a page's own boilerplate
    says nothing about the person, and the retriever filters the excerpt by name."""
    body = " ".join(text.split())
    if not body:
        return ""
    folded = body.lower().replace("ё", "е")
    positions = [found for found in (folded.find(word) for word in _words(words)) if found >= 0]
    if not positions:
        return body[:MAX_TEXT]
    start = max(0, min(positions) - EXCERPT_LEAD)
    return body[start : start + MAX_TEXT]


def _words(text: str) -> list[str]:
    return [word.lower().replace("ё", "е") for word in _WORD.findall(text) if len(word) > 2]


def _published_at(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        published = datetime.fromisoformat(value.strip())
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
