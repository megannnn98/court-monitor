"""Targeted, read-only search of a configured source: «find material about X».

Not discovery. A source adapter lists a feed's newest items to store them; a search here
asks one source for the items that hold some words, and stores nothing. A source that
can only list its feed has no search, and none is pretended for it.

What searches exist is configured by the application, never by a request: each search
is bound to one source of the registry, its address is built from that source alone,
and a query is only ever its words, sent as a query parameter. There is no way to make
a search fetch another address (no open-URL tool, ADR 0027).

Searches shipped:

- `TelegramChannelSearch`: the public web preview of a channel has a search box
  (`https://t.me/s/<channel>?q=<words>`); no account, no credentials. Only channels of
  `ingestion/sources/telegram/channels.csv` can be searched.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

import httpx
from selectolax.parser import HTMLParser

from ingestion.sources.telegram.article_parser import own_text_node
from ingestion.sources.telegram.channels import TelegramChannel, load_telegram_channels
from monitor_core.retry import retry_async, transient_http_error

logger = logging.getLogger("sources")

TELEGRAM_HOST = "t.me"
# Telegram's own rule for a public username.
_USERNAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{3,31}$")
MAX_QUERY_LENGTH = 200
MAX_TEXT = 2_000
DEFAULT_ATTEMPTS = 2
DEFAULT_BASE_DELAY_SECONDS = 0.5
DEFAULT_TIMEOUT_SECONDS = 15.0
USER_AGENT = "court-monitor-investigation/1.0"
# The channels an investigation searches unless configured otherwise: news and the human
# rights organisations whose posts name the persecuted.
DEFAULT_TELEGRAM_CHANNELS = ("ovdinfolive", "mediazzzona", "pzk_memorial", "politzekinfo")
TELEGRAM_CHANNELS_ENV = "INVESTIGATION_TELEGRAM_CHANNELS"


class SearchConfigurationError(ValueError):
    """A configured search names a source that is not in the registry."""


class SearchAnswerError(RuntimeError):
    """A source answered with something that is not its page of results."""


def _without_words(value: object) -> object:
    """A search's address as it may be logged: the channel, never the words asked."""
    if (
        isinstance(value, httpx.URL)
        and value.host == TELEGRAM_HOST
        and value.path.startswith("/s/")
        and value.query
    ):
        return value.copy_with(query=None)
    return value


class _SearchWordsOutOfLogs(logging.Filter):
    """httpx logs every request with its whole address at INFO, the `q` of a search
    included: a person's name would stand in the process log, which the investigation's
    own telemetry takes care never to write (a hash and a length)."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(_without_words(value) for value in record.args)
        return True


_HTTPX_LOGGER = logging.getLogger("httpx")
if not any(isinstance(kept, _SearchWordsOutOfLogs) for kept in _HTTPX_LOGGER.filters):
    _HTTPX_LOGGER.addFilter(_SearchWordsOutOfLogs())


@dataclass(frozen=True)
class SourceSearchQuery:
    text: str
    limit: int = 10


@dataclass(frozen=True)
class ExternalEvidence:
    """One item a source returned for a query, as the source shows it."""

    source: str
    source_name: str
    external_id: str
    url: str
    text: str
    published_at: datetime | None
    title: str | None = None


class SourceSearch(Protocol):
    @property
    def source(self) -> str:
        """The registry name of the source searched (`tg-ovdinfolive`)."""
        ...

    @property
    def title(self) -> str: ...

    async def search(self, query: SourceSearchQuery) -> list[ExternalEvidence]:
        """At most `query.limit` items. Raises on a failure the retry did not get past."""
        ...


def _clean(text: str) -> str:
    return " ".join(text.split())[:MAX_QUERY_LENGTH]


class TelegramChannelSearch:
    """One public channel's own search, through its web preview."""

    def __init__(
        self,
        channel: TelegramChannel,
        *,
        client: httpx.AsyncClient | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        attempts: int = DEFAULT_ATTEMPTS,
        base_delay_seconds: float = DEFAULT_BASE_DELAY_SECONDS,
    ) -> None:
        if not _USERNAME.match(channel.username):
            raise SearchConfigurationError(f"not a Telegram username: {channel.username!r}")
        if attempts < 1:
            raise ValueError("attempts must be at least 1")
        # None: a client per search, closed with it — a search runs on whatever event loop
        # its caller has. A caller's own client is the caller's to close.
        self._client = client
        self._timeout = httpx.Timeout(timeout_seconds)
        self._channel = channel
        self._attempts = attempts
        self._base_delay_seconds = base_delay_seconds

    @property
    def source(self) -> str:
        return self._channel.source_name

    @property
    def title(self) -> str:
        return self._channel.title

    @property
    def username(self) -> str:
        return self._channel.username

    def request_url(self, text: str) -> httpx.URL:
        """The one address this search ever asks: its channel's page, the words as `q`."""
        url = httpx.URL(f"https://{TELEGRAM_HOST}/s/{self._channel.username}", params={"q": text})
        if url.scheme != "https" or url.host != TELEGRAM_HOST:
            raise SearchConfigurationError(f"refused address: {url}")
        return url

    async def search(self, query: SourceSearchQuery) -> list[ExternalEvidence]:
        text = _clean(query.text)
        if not text:
            return []
        url = self.request_url(text)

        async def ask(client: httpx.AsyncClient) -> bytes:
            # A redirect is a channel without a web preview: nothing to search.
            response = await client.get(url, follow_redirects=False, timeout=self._timeout)
            if response.is_redirect:
                return b""
            response.raise_for_status()
            kind = response.headers.get("content-type", "")
            if kind and "html" not in kind.lower():
                # Not the page of posts: read as a page it would be «nothing found».
                raise SearchAnswerError(f"{self.source}: not a page of results ({kind[:40]})")
            return response.content

        async def with_retry(client: httpx.AsyncClient) -> bytes:
            return await retry_async(
                lambda: ask(client),
                attempts=self._attempts,
                should_retry=transient_http_error,
                delay_seconds=lambda failures: self._base_delay_seconds * (2 ** (failures - 1)),
                sleep=asyncio.sleep,
            )

        if self._client is not None:
            content = await with_retry(self._client)
        else:
            async with httpx.AsyncClient(headers={"User-Agent": USER_AGENT}) as client:
                content = await with_retry(client)
        logger.info(
            "event=source_search source=%s results_page_bytes=%d", self.source, len(content)
        )
        return self.parse(content, limit=query.limit)

    def parse(self, content: bytes, *, limit: int) -> list[ExternalEvidence]:
        """The channel's own posts on the page, newest first; a forwarded or another
        channel's post is not this channel's."""
        if not content:
            return []
        tree = HTMLParser(content.replace(b"<br/>", b"\n").replace(b"<br>", b"\n"))
        found: list[ExternalEvidence] = []
        for node in tree.css("div.tgme_widget_message[data-post]"):
            channel, _, raw_id = (node.attributes.get("data-post") or "").partition("/")
            if channel.lower() != self._channel.username.lower() or not raw_id.isdigit():
                continue
            text_node = own_text_node(node)
            if text_node is None:
                continue
            body = " ".join(text_node.text().split())[:MAX_TEXT]
            if not body:
                continue
            time_node = node.css_first(".tgme_widget_message_date time[datetime]")
            published = (time_node.attributes.get("datetime") if time_node else None) or None
            found.append(
                ExternalEvidence(
                    source=self.source,
                    source_name=self.title,
                    external_id=raw_id,
                    url=f"https://{TELEGRAM_HOST}/{self._channel.username}/{raw_id}",
                    text=body,
                    published_at=datetime.fromisoformat(published) if published else None,
                    title=body.split(". ", 1)[0][:120],
                )
            )
        found.sort(key=lambda item: int(item.external_id), reverse=True)
        return found[:limit]


def configured_telegram_channels(
    env: Mapping[str, str] | None = None,
    *,
    registry: Sequence[TelegramChannel] | None = None,
) -> list[TelegramChannel]:
    """`INVESTIGATION_TELEGRAM_CHANNELS` (comma-separated usernames, or «none»), each of
    which must be a channel of the registry; the default four otherwise."""
    env = os.environ if env is None else env
    known = {channel.username.lower(): channel for channel in registry or load_telegram_channels()}
    raw = (env.get(TELEGRAM_CHANNELS_ENV) or "").strip()
    if raw.lower() == "none":
        return []
    names = [name.strip() for name in raw.split(",") if name.strip()] or list(
        DEFAULT_TELEGRAM_CHANNELS
    )
    unknown = [name for name in names if name.lower() not in known]
    if unknown:
        raise SearchConfigurationError(
            f"{TELEGRAM_CHANNELS_ENV}: not a channel of the registry: {', '.join(unknown)}"
        )
    return [known[name.lower()] for name in dict.fromkeys(names)]


def telegram_searches(
    channels: Sequence[TelegramChannel], *, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
) -> list[TelegramChannelSearch]:
    return [TelegramChannelSearch(channel, timeout_seconds=timeout_seconds) for channel in channels]
