"""An in-process Tavily Remote MCP server for the tests: the official SDK on both ends.

Nothing here reaches the internet. The server is a real `MCPServer` speaking Streamable
HTTP over an ASGI transport, and the client is the real `streamable_http_client`, so what
the tests exercise is the SDK's own handshake, tool listing, tool call and content
parsing — only the network between the two ends is gone.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import time
from collections.abc import AsyncIterator, Callable, Iterator, Mapping
from contextlib import asynccontextmanager, contextmanager
from typing import Any

import httpx2
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings

from ingestion.web_search import TAVILY_MCP_URL

HOST = "http://mcp.test"
ENDPOINT = f"{HOST}/mcp"
KEY = "tvly-a-key-nobody-must-see"
# How long a test waits for a loopback server to accept connections, in hundredths of a
# second: generous for a machine that is busy, short enough to fail rather than hang.
_STARTUP_CHECKS = 500

# The schema Tavily's `tavily_search` really publishes (measured 2026-10-14). The fake
# publishes it so that the tests check the client's arguments against the same shape the
# real server publishes.
SEARCH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "query": {"type": "string"},
        "max_results": {"type": "integer", "default": 5},
        "search_depth": {
            "type": "string",
            "enum": ["basic", "advanced", "fast", "ultra-fast"],
            "default": "basic",
        },
        "include_raw_content": {"type": "boolean", "default": False},
        "start_date": {"type": "string", "default": ""},
        "end_date": {"type": "string", "default": ""},
    },
    "required": ["query"],
    "additionalProperties": False,
}

# What the server answers with: a list of results, the query it ran and the request id
# Tavily's MCP returns. The fake declares this as the tool's structured output, so the
# client reads the same structured content it reads from the real server.
ANSWER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "query": {"type": "string"},
        "results": {"type": "array", "items": {"type": "object"}},
        "request_id": {"type": "string"},
        "response_time": {"type": "number"},
    },
    "required": ["results"],
}


class Recorder:
    """What the fake server was asked, and how often."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.authorizations: list[str | None] = []

    @property
    def last(self) -> dict[str, Any]:
        return self.calls[-1]


def _transport_security() -> TransportSecuritySettings:
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=False, allowed_hosts=["*"], allowed_origins=["*"]
    )


@asynccontextmanager
async def fake_tavily_mcp(
    answers: Any,
    *,
    tool: str = "tavily_search",
    publish_tool: bool = True,
    expected_key: str | None = KEY,
    key: str = KEY,
    delay_seconds: float = 0.0,
    delay_forever: bool = False,
    text_only: bool = False,
) -> AsyncIterator[tuple[httpx2.AsyncClient, Recorder]]:
    """A Tavily MCP server in this process, and the client that reaches it.

    `answers` is what the tool answers with, one body per call; a callable is given the
    arguments instead, and may return a body, a string, or raise. `publish_tool=False`
    builds a server that publishes no search tool at all; the client is given `key` as
    its bearer header and the server accepts only `expected_key`; `delay_forever` makes
    the tool never answer; `text_only` makes it answer in a content block and no
    structured content, the other shape a server may reply in.
    """
    recorder = Recorder()
    served, guarded = _build(
        answers,
        recorder,
        tool=tool,
        publish_tool=publish_tool,
        expected_key=expected_key,
        delay_seconds=delay_seconds,
        delay_forever=delay_forever,
        text_only=text_only,
    )
    async with (
        _lifespan(served),
        httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=guarded),  # type: ignore[arg-type]
            base_url=HOST,
            headers={"Authorization": f"Bearer {key}"},
        ) as client,
    ):
        yield client, recorder


def _build(
    answers: Any,
    recorder: Recorder,
    *,
    tool: str,
    publish_tool: bool,
    expected_key: str | None,
    delay_seconds: float,
    delay_forever: bool,
    text_only: bool,
) -> tuple[Any, Any]:
    """The Tavily MCP server: the app whose lifespan has to run, and the guarded app that
    is actually served — the one only a right bearer header reaches."""
    queued = list(answers) if isinstance(answers, list) else []

    async def answer_one(arguments: Mapping[str, Any]) -> Any:
        if delay_forever:
            await asyncio.sleep(3600)
        elif delay_seconds:
            await asyncio.sleep(delay_seconds)
        recorder.calls.append(dict(arguments))
        if not callable(answers):
            body = queued.pop(0) if queued else {}
        else:
            body = answers(arguments)
            if inspect.isawaitable(body):
                body = await body
        if isinstance(body, BaseException):
            raise body
        return body

    server = MCPServer(name="fake-tavily", version="0.0.0")
    if publish_tool:
        server.add_tool(
            _tool(tool, answer_one, text_only=text_only),
            name=tool,
            description="Search the web",
        )
    app = server.streamable_http_app(transport_security=_transport_security())

    async def guard(scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] == "http":
            header = next(
                (
                    (k, v)
                    for k, v in scope.get("headers", ())
                    if k.decode().lower() == "authorization"
                ),
                (None, None),
            )
            recorder.authorizations.append(header[1].decode() if header[1] else None)
            if expected_key is not None and header[1] != f"Bearer {expected_key}".encode():
                await send(
                    {
                        "type": "http.response.start",
                        "status": 401,
                        "headers": [(b"content-type", b"application/json")],
                    }
                )
                await send({"type": "http.response.body", "body": b'{"detail":"invalid key"}'})
                return
        await app(scope, receive, send)

    return app, guard


@contextmanager
def served_tavily_mcp(answers: Any, **options: Any) -> Iterator[tuple[str, Recorder]]:
    """The same server on a loopback port, for a test that is not itself asynchronous.

    A run is driven by `asyncio.run` from ordinary synchronous test code, and a server
    bound to one event loop cannot follow it into another; a server on a port can. What
    the client sees — the handshake, the tools, the answers — is the same either way.
    """
    import threading

    import uvicorn

    recorder = Recorder()
    _served, guarded = _build(
        answers,
        recorder,
        tool="tavily_search",
        publish_tool=True,
        expected_key=options.get("expected_key", KEY),
        delay_seconds=options.get("delay_seconds", 0.0),
        delay_forever=options.get("delay_forever", False),
        text_only=options.get("text_only", False),
    )
    running = uvicorn.Server(
        uvicorn.Config(app=guarded, host="127.0.0.1", port=0, log_level="warning")
    )
    thread = threading.Thread(target=running.run, daemon=True)
    thread.start()
    try:
        for _ in range(_STARTUP_CHECKS):
            if running.started:
                break
            time.sleep(0.01)
        assert running.started and running.servers, "the fake MCP server did not start"
        port = running.servers[0].sockets[0].getsockname()[1]
        yield f"http://127.0.0.1:{port}/mcp", recorder
    finally:
        running.should_exit = True
        thread.join(timeout=10)


@asynccontextmanager
async def _lifespan(app: Any) -> AsyncIterator[None]:
    """The server's lifespan, in a task of its own from the first line to the last.

    It opens the session manager's task group and closes it again, and a cancel scope may
    only be closed by the task that opened it. The caller's task is busy with the client,
    so the lifespan gets one and keeps it for as long as the server is up.
    """
    ready = asyncio.Event()
    stop = asyncio.Event()

    async def serve() -> None:
        async with app.router.lifespan_context(app):
            ready.set()
            await stop.wait()

    task = asyncio.ensure_future(serve())
    try:
        await ready.wait()
        yield
    finally:
        stop.set()
        await task


def _tool(name: str, answer: Callable[[Mapping[str, Any]], Any], *, text_only: bool) -> Any:
    """A tool whose published schema is Tavily's own, so the client's argument mapping is
    checked against the real shape rather than against a simplified stand-in."""

    async def tavily_search(
        query: str,
        max_results: int = 5,
        search_depth: str = "basic",
        include_raw_content: bool = False,
        start_date: str = "",
        end_date: str = "",
    ) -> dict[str, Any]:
        body = await answer(
            {
                "query": query,
                "max_results": max_results,
                "search_depth": search_depth,
                "include_raw_content": include_raw_content,
                "start_date": start_date,
                "end_date": end_date,
            }
        )
        return body if isinstance(body, dict) else {"answer": body}

    tavily_search.__name__ = name
    if text_only:
        # A tool that answers in one text block and no structured content: the other
        # shape a server may reply in, and the one the client falls back to.
        import inspect

        async def as_text(
            query: str,
            max_results: int = 5,
            search_depth: str = "basic",
            include_raw_content: bool = False,
            start_date: str = "",
            end_date: str = "",
        ) -> str:
            body = await tavily_search(
                query, max_results, search_depth, include_raw_content, start_date, end_date
            )
            return json.dumps(body, ensure_ascii=False)

        as_text.__name__ = name
        as_text.__signature__ = inspect.signature(tavily_search).replace(  # type: ignore[attr-defined]
            return_annotation=str
        )
        return as_text
    return tavily_search


def results(*pages: Mapping[str, Any]) -> dict[str, Any]:
    """A Tavily-shaped answer: a list of results and the request id the server returns."""
    return {"query": "", "results": list(pages), "request_id": "req-1", "response_time": 0.1}


def page(
    *,
    url: str = "https://news.example.test/boncler-1",
    title: str = "Суд над адвокатом",
    content: str = "Кратко: Мария Бонцлер арестована.",
    raw_content: str | None = "Городской суд оставил Марию Бонцлер под стражей.",
    published_date: str | None = None,
) -> dict[str, Any]:
    found: dict[str, Any] = {"url": url, "title": title, "content": content, "score": 0.5}
    if raw_content is not None:
        found["raw_content"] = raw_content
    if published_date is not None:
        found["published_date"] = published_date
    return found


__all__ = [
    "ENDPOINT",
    "HOST",
    "KEY",
    "TAVILY_MCP_URL",
    "Recorder",
    "fake_tavily_mcp",
    "page",
    "results",
    "served_tavily_mcp",
]
