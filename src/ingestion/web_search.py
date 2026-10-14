"""The web as one more configured source of the read-only search: Tavily, over its own
official remote MCP server.

A commercial search API stands in for what a project's own channels do not cover —
the open web, read as its results. What keeps it the same kind of object as
`TelegramChannelSearch`: one address (the MCP endpoint, a constant), the words travel
only in the body of a tool call, nothing is stored, extracted or grouped, and the full
text comes from the answer itself rather than from a fetch this code makes. There is no
page loader here: the backend only ever talks to `mcp.tavily.com`, so there is no URL for
a found text to steer it at (ADR 0027).

**Why MCP and not REST.** Court Monitor is an MCP *client* here: the official Python SDK
(`mcp`) speaks Streamable HTTP to `https://mcp.tavily.com/mcp`, authenticating with
`Authorization: Bearer <TAVILY_API_KEY>`. The key is a header, never a query parameter and
never part of an address, and the key is kept out of `repr`, out of an exception, out of a
log line and out of a stored run.

**What the server really publishes** (measured with a live handshake on 2026-10-14,
`tavily-mcp` 4.0.4, protocol `2025-11-25`): the tools `tavily_search`,
`tavily_extract`, `tavily_crawl`, `tavily_map`, `tavily_research`. `tavily_search`
publishes `query`, `max_results`, `search_depth`, `topic`, `time_range`, `include_images`,
`include_image_descriptions`, `include_raw_content`, `include_domains`,
`exclude_domains`, `country`, `include_favicon`, `start_date`, `end_date`,
`exact_match` — and nothing else. In particular it does **not** publish
`include_published_date` or `include_usage`, so results carry no publication date and the
answer reports no credits. Nothing here is assumed: the arguments are filtered against
the schema the server itself published in `list_tools()`, and a parameter it does not
publish is simply not sent.

The words asked are never logged — only the source, how many results came back, and what
the run's own request id was.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import TYPE_CHECKING, Any, TypeGuard
from urllib.parse import unquote, urlsplit

import httpx2

if TYPE_CHECKING:
    from mcp.shared.exceptions import MCPError

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
from monitor_core.retry import retry_async

logger = logging.getLogger("sources")

# The one address this search ever asks. A constant, never built from a found text and
# never carrying the key.
TAVILY_MCP_URL = "https://mcp.tavily.com/mcp"
TAVILY_API_KEY_ENV = "TAVILY_API_KEY"
# The tool the server publishes for a search. Verified by `list_tools()` on every call:
# a server that stopped publishing it is a failed search, not a silent one.
SEARCH_TOOL = "tavily_search"
# Tavily answers 0–20 results; a search asks for what its own retriever wanted.
MAX_RESULTS = 10
# «basic» is one credit; «advanced» is two and buys a slower, longer crawl. Which one is
# used is a setting, and the benchmark measures both (see `docs/wiki/Pipeline.md`).
SEARCH_DEPTH = "basic"
SEARCH_DEPTH_ENV = "INVESTIGATION_WEB_SEARCH_DEPTH"
SEARCH_DEPTHS = ("basic", "advanced", "fast", "ultra-fast")
# A `basic` search answers in under a second and a page's own text runs to a megabyte and
# more, so the SDK's default 1 MiB cap on one server-sent event is raised: without it a
# search that did return the page fails while reading the answer.
MAX_SSE_EVENT_BYTES = 32 * 1024 * 1024
# A session is opened and closed inside the one search that uses it. The SDK's transport
# and session hold anyio task groups, which a search running in a task of its own may only
# enter and leave there — so one session per search it is. Reusing one across searches is
# possible (a call may be made from any task) but needs a task of its own to own it, an
# explicit shutdown and a reconnection after the server drops the session; that buys the
# handshake and the tool listing of one search, measured at ~0.8 s, and is not taken on
# here. What a search does pay is `initialize` + `list_tools` + `call_tool` + `DELETE`.
HANDSHAKE_NOTE = " mcp_session=short_lived"
# The floor under the SDK's per-request read timeout: a search slower than this is not a
# search any more, and the SDK's POST task would otherwise wait for ever.
MIN_READ_TIMEOUT_SECONDS = 10.0
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


class _AskedWordsOutOfLogs(logging.Filter):
    """The MCP client logs every call's arguments at DEBUG, and the arguments of a search
    are the words a person was looked up by. The investigation's own telemetry writes a
    hash and a length of a query and never the query (ADR 0027); the transport's log must
    not be the place the name comes back. Only the `query` argument is taken out."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(_without_asked_words(value) for value in record.args)
        elif isinstance(record.args, (Mapping, str, bytes)):
            # One mapping or one message passed as `%(x)s`: not a tuple, and just as able
            # to carry the words.
            record.args = (_without_asked_words(record.args),)
        # The SDK writes most of its call lines already formatted, so the words are in the
        # message itself and not in the arguments.
        if isinstance(record.msg, str):
            record.msg = str(_without_asked_words(record.msg))
        return True


# «query»: the asked words, in a repr or in a formatted message. Both quote forms, and a
# name with an apostrophe in it («В'ячеслав») does not end the match at the apostrophe.
_ASKED = re.compile(r"(['\"]query['\"]\s*:\s*)(['\"]).*?\2", re.DOTALL)
_WHOLE_QUERY = re.compile(r"\bquery=[^&,\s]*", re.IGNORECASE)
# The SDK's own loggers. `mcp.client.session` writes to a logger of its own name,
# `client`, which is not under `mcp` — the filter is attached to every one of them by
# name, and so is the level.
_SDK_LOGGERS = ("mcp", "client")


def _without_asked_words(value: object) -> object:
    """The `query` of a call, wherever the SDK put it: the tool's arguments are nested
    inside the request, and the answer echoes the query back as text."""
    if isinstance(value, Mapping):
        return {
            key: ("<query>" if key == "query" else _without_asked_words(held))
            for key, held in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_without_asked_words(held) for held in value]
    if isinstance(value, str):
        return _WHOLE_QUERY.sub("query=<query>", _ASKED.sub(r"\1\2<query>\2", value))
    return value


def _quiet_mcp_words() -> None:
    """The SDK's own loggers: no DEBUG line about a call, and whatever is logged anyway
    has the words taken out of it.

    The transport writes every message it sends and every message it receives at DEBUG —
    the whole answer of a search, a page's own text and all — and one search's DEBUG
    trace is a page the reader of the result was never shown. So the level goes first and
    the filter is the second line: a filter belongs to the logger whose record it reads,
    not to an ancestor of it, so each of the SDK's loggers gets one. They are created as
    the SDK is imported, which is where this is done.
    """
    for name in list(logging.Logger.manager.loggerDict):
        root = name.split(".", 1)[0]
        if root not in _SDK_LOGGERS:
            continue
        logger_ = logging.getLogger(name)
        logger_.setLevel(max(logger_.level or logging.NOTSET, logging.INFO))
        if not any(isinstance(kept, _AskedWordsOutOfLogs) for kept in logger_.filters):
            logger_.addFilter(_AskedWordsOutOfLogs())


class TavilyMcpError(RuntimeError):
    """The official Tavily MCP server could not answer one search.

    Raised for a transport failure, a protocol failure and an answer that is not a list
    of results alike, so the investigation turns all three into one warning and one
    failed search. The key is never in its message: only the exception's class reaches a
    log, and this text reaches none.
    """


# The SDK's own JSON-RPC error codes, as `mcp_types` publishes them. The client turns an
# HTTP status of the server into `INTERNAL_ERROR` and a stream that ended without an
# answer into `CONNECTION_CLOSED` or `REQUEST_TIMEOUT`; the status itself is lost.
_INTERNAL_ERROR = -32603
_CONNECTION_CLOSED = -32000
_REQUEST_TIMEOUT = -32001
# What a status has to be for the same call to be worth making again.
_TRANSIENT_STATUS = frozenset({408, 409, 425, 429})


class _LastStatus:
    """The status of the last response the server gave, kept beside the call.

    The SDK folds every HTTP failure of the server into one JSON-RPC error and keeps the
    status to itself, so a rejected key and a rate limit arrive alike. Only the HTTP client
    still sees the status: a hook on the response records it here, and the decision
    whether to ask again is made from it. Without it, nothing but a timeout is worth
    retrying, and a Tavily that is rate-limiting is exactly the case that is.
    """

    def __init__(self) -> None:
        self.status: int | None = None

    async def record(self, response: Any) -> None:
        """An HTTP client's response hook, which the client awaits."""
        self.status = int(response.status_code)

    def forget(self) -> None:
        self.status = None

    @property
    def transient(self) -> bool:
        status = self.status
        return status is not None and (status in _TRANSIENT_STATUS or status >= 500)


def _transient(exc: BaseException, seen: _LastStatus) -> bool:
    """Whether the failure is worth asking the server again.

    A timeout, a refused, reset or half-open connection, a rate limit and Tavily's own
    server errors are transient. A rejected key, a tool the server does not publish, an
    answer whose shape is not a search's and every protocol failure are not: the same
    call answers the same way again, and a retry that cannot succeed only spends the run's
    time and the plan's searches.

    A JSON-RPC error stands for two different things — a status the SDK folded into it
    (`INTERNAL_ERROR`) and a stream that ended under the call (`CONNECTION_CLOSED`,
    `REQUEST_TIMEOUT`) — so the first is decided by the status the HTTP client saw, and
    the second by what it says about itself.
    """
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError, ConnectionError, OSError)):
        return True
    if isinstance(exc, (httpx2.TimeoutException, httpx2.NetworkError, httpx2.RemoteProtocolError)):
        return True
    code = getattr(getattr(exc, "response", None), "status_code", None)
    if isinstance(code, int):
        return code in _TRANSIENT_STATUS or code >= 500
    if _is_mcp_error(exc):
        if exc.code in {_CONNECTION_CLOSED, _REQUEST_TIMEOUT}:
            return True
        # A status the SDK folded into a protocol error. Known and transient: retry.
        # Known and not: the key was refused, the tool is not published — do not.
        # Unseen (no hook, or the failure was not HTTP at all): do not, because a
        # retry that may hit a refused key spends the run's time for nothing.
        return exc.code == _INTERNAL_ERROR and seen.transient
    return False


def _only(exc: BaseException) -> BaseException:
    """The one failure inside a group, where there is only one.

    The SDK's transport and session end in anyio task groups, and a task group re-raises
    what ended the block as an `ExceptionGroup` even when a single thing caused it — so
    the `SearchAnswerError` this code raised itself comes back wrapped. A group of one is
    that one thing; a group of several is not one failure a retry can judge.
    """
    if isinstance(exc, BaseExceptionGroup) and len(exc.exceptions) == 1:
        return _only(exc.exceptions[0])
    return exc


def _is_mcp_error(exc: BaseException) -> TypeGuard[MCPError]:
    """Whether this is the SDK's own protocol error. Imported lazily: the SDK is only
    imported when a search is actually made."""
    from mcp.shared.exceptions import MCPError

    return isinstance(exc, MCPError)


class TavilyMcpSearch:
    """Tavily's official remote MCP server, asked by words: one more `SourceSearch`.

    A session per search, opened and closed inside the search's own task (§HANDSHAKE_NOTE).
    """

    def __init__(
        self,
        api_key: str,
        *,
        url: str = TAVILY_MCP_URL,
        search_tool: str = SEARCH_TOOL,
        search_depth: str = SEARCH_DEPTH,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        attempts: int = DEFAULT_ATTEMPTS,
        base_delay_seconds: float = DEFAULT_BASE_DELAY_SECONDS,
        client: Any | None = None,
    ) -> None:
        if not api_key.strip():
            raise ValueError("a Tavily search needs an API key")
        if search_depth not in SEARCH_DEPTHS:
            raise ValueError(f"search_depth must be one of {', '.join(SEARCH_DEPTHS)}")
        if attempts < 1:
            raise ValueError("attempts must be at least 1")
        self._api_key = api_key
        self._url = url
        self._search_tool = search_tool
        self._search_depth = search_depth
        # None: a client per search, closed with it — a search runs on whatever event
        # loop its caller has, and the SDK's client is bound to the one it is made on.
        # A caller's own client is the caller's to close.
        self._client = client
        self._timeout = timeout_seconds
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

    @property
    def search_depth(self) -> str:
        return self._search_depth

    @property
    def timeout_seconds(self) -> float:
        """What one search may take, whole or not: the run's own search timeout."""
        return self._timeout

    # ── what is asked ────────────────────────────────────────────────────────────

    def request_arguments(
        self,
        text: str,
        *,
        limit: int,
        published_from: datetime | None,
        published_to: datetime | None,
    ) -> dict[str, object]:
        """Every argument this search would like to send, before the schema has a say."""
        # A query that keeps to a window of dates is asked within it: days, as the server
        # documents them.
        window = {
            key: day.date().isoformat()
            for key, day in (("start_date", published_from), ("end_date", published_to))
            if day is not None
        }
        return {
            **window,
            "query": text,
            "max_results": max(0, min(limit, MAX_RESULTS)),
            "search_depth": self._search_depth,
            # The page's own text, so no second fetch of what the answer already read.
            "include_raw_content": True,
        }

    @staticmethod
    def tool_arguments(
        schema: Mapping[str, Any], wanted: Mapping[str, object]
    ) -> dict[str, object]:
        """What of `wanted` the server's own schema accepts.

        The tool is described by `list_tools()`, not remembered: a parameter the published
        schema does not list is not sent, whether it is absent, deprecated or one this
        code has not heard of.
        """
        properties = schema.get("properties")
        if not isinstance(properties, Mapping):
            return dict(wanted)
        return {key: value for key, value in wanted.items() if key in properties}

    # ── the session ──────────────────────────────────────────────────────────────

    def _http_client(self, seen: _LastStatus | None = None) -> tuple[Any, bool]:
        """The SDK's own HTTP client, carrying the key as a bearer header and nothing
        else, or a caller's own client. Ours also records what status the server gave, so
        that a rate limit can be told from a refused key after the SDK folded both into
        one protocol error."""
        if self._client is not None:
            return self._client, False
        hooks = {"response": [seen.record]} if seen is not None else None
        return (
            httpx2.AsyncClient(
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "User-Agent": "court-monitor-investigation/1.0",
                },
                timeout=self._timeout,
                event_hooks=hooks,
            ),
            True,
        )

    async def _with_session(self, client: Any, work: Any) -> Any:
        """One MCP session, opened and closed inside one `async with`, in one task.

        Not `__aenter__` in a function and `__aexit__` in another: the SDK's transport
        and session each hold an anyio task group, and a context manager the frame does
        not hold is collected — which closes the generator it wrapped and cancels the
        search that is still using it. Here the frame holds both for as long as the call
        runs, a cancelled search closes them with the cancellation rather than after it,
        and each of them is told what ended the block.
        """
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client

        _quiet_mcp_words()
        async with streamable_http_client(
            self._url, http_client=client, max_sse_event_size=MAX_SSE_EVENT_BYTES
        ) as streams:
            read, write = streams[0], streams[1]
            async with ClientSession(
                read,
                write,
                # The SDK's POST task swallows a dropped connection and leaves the
                # request unanswered, so without a read timeout a call can wait for
                # ever. This is the bound the run itself gives one search.
                read_timeout_seconds=max(self._timeout, MIN_READ_TIMEOUT_SECONDS),
            ) as session:
                await session.initialize()
                return await work(session)

    async def tools(self) -> list[Mapping[str, Any]]:
        """What the server publishes: every tool with the schema it published it under."""
        seen = _LastStatus()
        connection, own = self._http_client(seen)
        try:
            found: list[Mapping[str, Any]] = await self._with_session(connection, self._read_tools)
            return found
        finally:
            if own:
                await connection.aclose()

    async def _read_tools(self, session: Any) -> list[Mapping[str, Any]]:
        listed = await session.list_tools()
        return [
            {"name": tool.name, "description": tool.description, "input_schema": tool.input_schema}
            for tool in listed.tools
        ]

    async def search(self, query: SourceSearchQuery) -> list[ExternalEvidence]:
        text = " ".join(query.text.split())[:MAX_QUERY_LENGTH]
        if not text:
            return []

        def asked(session: Any) -> Any:
            return self._call(session, text, query)

        seen = _LastStatus()
        found: list[ExternalEvidence]
        request_id: str | None
        try:
            found, request_id = await retry_async(
                lambda: self._attempt(seen, asked),
                attempts=self._attempts,
                should_retry=lambda exc: _transient(_only(exc), seen),
                delay_seconds=lambda failures: self._base_delay_seconds * (2 ** (failures - 1)),
                sleep=asyncio.sleep,
            )
        except BaseException as exc:
            cause = _only(exc)
            if isinstance(cause, SearchAnswerError):
                # The server answered and what it answered is not a search's. Retrying
                # buys the same answer, so this one is raised as it is: the run turns it
                # into a warning and a failed search, not into a lost run.
                raise cause
            if not isinstance(cause, Exception):
                # A cancellation or a signal, wrapped or not: it is not ours to turn into
                # a failed search, and the caller is waiting for it.
                raise cause
            # Only the class of the cause is ever logged: a transport error carries the
            # request it made, and the request carries the person's name.
            raise TavilyMcpError(
                f"{self.source}: the search failed ({type(cause).__name__})"
            ) from exc
        # Never the words: a person's name does not stand in the process log.
        logger.info(
            "event=source_search source=%s results=%d%s%s",
            self.source,
            len(found),
            HANDSHAKE_NOTE,
            f" request_id={request_id}" if request_id else "",
        )
        return found

    async def _attempt(self, seen: _LastStatus, asked: Any) -> Any:
        """One try: a fresh status, a fresh session, and a client of our own to carry it."""
        seen.forget()
        connection, own = self._http_client(seen)
        try:
            return await self._with_session(connection, asked)
        finally:
            if own:
                await connection.aclose()

    async def _call(self, session: Any, text: str, query: SourceSearchQuery) -> Any:
        """One search inside one session: the tool the server publishes, then its answer.

        The tool is chosen from `list_tools()` and not from what this code remembers, and
        the arguments from the schema that tool published under.
        """
        published = await self._read_tools(session)
        tool = next((entry for entry in published if entry["name"] == self._search_tool), None)
        if tool is None:
            raise SearchAnswerError(
                f"{self.source}: the server publishes no {self._search_tool}; "
                f"it publishes {', '.join(sorted(str(e['name']) for e in published)) or 'nothing'}"
            )
        schema = tool.get("input_schema")
        arguments = self.tool_arguments(
            schema if isinstance(schema, Mapping) else {},
            self.request_arguments(
                text,
                limit=query.limit,
                published_from=query.published_from,
                published_to=query.published_to,
            ),
        )
        answered = await session.call_tool(self._search_tool, arguments)
        body = self.answer_of(answered)
        return self.parse(body, text=text, limit=query.limit), _request_id(body)

    @staticmethod
    def answer_of(answered: Any) -> Mapping[str, Any]:
        """The answer of a tool call, as a mapping that holds a list of results.

        Tavily's server sends that list as structured content and repeats the same object
        as one text block. A server that has no structured output sends the object in the
        text block alone; the SDK then wraps a tool's string return in a single
        `{"result": …}` key, which is unwrapped here rather than read as the answer.

        A call the server itself marked failed, and a text that is not a search's, are
        both a failed search.
        """
        if getattr(answered, "is_error", False):
            raise SearchAnswerError("tavily: the search tool answered with a failure")
        for candidate in _candidates(answered):
            if _holds_results(candidate):
                return candidate
        raise SearchAnswerError("tavily: the answer holds no list of results")

    # ── what came back ───────────────────────────────────────────────────────────

    def parse(self, body: Mapping[str, Any], *, text: str, limit: int) -> list[ExternalEvidence]:
        """The results as Tavily shows them: each with the site's own text, opened at the
        words that were asked for."""
        if not _holds_results(body):
            raise SearchAnswerError(f"{self.source}: the answer holds no list of results")
        found: list[ExternalEvidence] = []
        for entry in body["results"]:
            if not isinstance(entry, Mapping):
                continue
            item = self._evidence(entry, text=text)
            if item is not None:
                found.append(item)
        return found[:limit]

    def _evidence(self, entry: Mapping[str, Any], *, text: str) -> ExternalEvidence | None:
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
            source_name=_host(url) or self.title,
            external_id=_canonical(url),
            url=url,
            text=body,
            # The published tool takes no `include_published_date`: a result it returns
            # has no date. One that carries a date all the same is read as the REST API
            # wrote it, so a server that starts publishing them is used as it stands.
            published_at=_published_at(entry.get("published_date")),
            title=(str(entry["title"]).strip() or None) if entry.get("title") else None,
        )


def _holds_results(body: object) -> bool:
    """Whether this mapping is a search's answer: it holds a list of results."""
    return isinstance(body, Mapping) and isinstance(body.get("results"), list)


def _candidates(answered: Any) -> Iterator[Mapping[str, Any]]:
    """What a tool call could have answered with, best first: the structured content, then
    every text block decoded. A tool that returned one string has its answer under a
    single key of the structured content, so what that string holds is read too."""
    structured = getattr(answered, "structured_content", None)
    decoded_blocks: list[Mapping[str, Any]] = []
    if isinstance(structured, Mapping) and structured:
        yield structured
    for block in getattr(answered, "content", ()) or ():
        raw = getattr(block, "text", None)
        if not isinstance(raw, str) or not raw.strip():
            continue
        try:
            decoded = json.loads(raw)
        except ValueError:
            continue
        if isinstance(decoded, Mapping):
            decoded_blocks.append(decoded)
    yield from decoded_blocks
    for wrapper in (structured, *decoded_blocks):
        if not isinstance(wrapper, Mapping) or len(wrapper) != 1:
            continue
        only = next(iter(wrapper.values()))
        if not isinstance(only, str):
            continue
        try:
            unwrapped = json.loads(only)
        except ValueError:
            continue
        if isinstance(unwrapped, Mapping):
            yield unwrapped


def _host(url: str) -> str | None:
    return urlsplit(url).hostname


def _request_id(body: Mapping[str, Any]) -> str | None:
    value = body.get("request_id")
    return value if isinstance(value, str) and value else None


def _canonical(url: str) -> str:
    """The address as one site writes it alike: the id the same page has everywhere."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower().removeprefix("www.")
    # The path is left as the site writes it: it may be case-sensitive, and two addresses
    # that differ in the case of a path are two pages however alike they look.
    path = parts.path.rstrip("/")
    return f"{host}{path}"


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
    """When the page was published, as a date is written: RFC 2822 («Tue, 21 Jul 2026
    00:00:00 GMT»), or ISO. The published MCP tool asks for no date and returns none, so
    this is read only when a result carries one."""
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
    env: Mapping[str, str] | None = None,
    *,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    attempts: int = DEFAULT_ATTEMPTS,
) -> TavilyMcpSearch | None:
    """The web search of the run, if a key is configured; None without `TAVILY_API_KEY`.

    Without a key there is no search at all: nothing is built, nothing is connected, and
    a run that asked the web asks nothing and says so.
    """
    env = os.environ if env is None else env
    api_key = (env.get(TAVILY_API_KEY_ENV) or "").strip()
    if not api_key:
        return None
    depth = (env.get(SEARCH_DEPTH_ENV) or "").strip() or SEARCH_DEPTH
    if depth not in SEARCH_DEPTHS:
        raise ValueError(f"{SEARCH_DEPTH_ENV} must be one of {', '.join(SEARCH_DEPTHS)}")
    return TavilyMcpSearch(
        api_key,
        search_depth=depth,
        timeout_seconds=timeout_seconds,
        attempts=attempts,
    )


__all__ = [
    "MAX_RESULTS",
    "SEARCH_DEPTH",
    "SEARCH_DEPTH_ENV",
    "SEARCH_TOOL",
    "TAVILY_API_KEY_ENV",
    "TAVILY_MCP_URL",
    "TavilyMcpError",
    "TavilyMcpSearch",
    "tavily_search_from_env",
]
