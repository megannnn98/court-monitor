"""Tests: source availability probing (incl. timeout / DNS handling)."""

from __future__ import annotations

from pathlib import Path

import httpx

from court_monitor.config.registry import SourceRegistryEntry
from court_monitor.domain.models import FetchHealth
from court_monitor.sources.http_client import HttpResponse
from court_monitor.sources.probe import (
    AVAILABLE,
    INVALID_URL,
    TEMPORARILY_UNAVAILABLE,
    UNSUPPORTED,
    probe_source,
)


class _FakeClient:
    """A stand-in for HttpClient returning a canned HttpResponse or raising."""

    def __init__(self, *, response: HttpResponse | None = None, exc: Exception | None = None) -> None:
        self._response = response
        self._exc = exc

    def get(self, url: str) -> HttpResponse:  # noqa: ARG002
        if self._exc is not None:
            raise self._exc
        assert self._response is not None
        return self._response

    def close(self) -> None:
        pass

    def __enter__(self) -> _FakeClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def _entry(source_type: str = "telegram", username: str | None = "abc", url: str | None = None) -> SourceRegistryEntry:
    return SourceRegistryEntry(
        id=username or "x",
        name=username or "x",
        url=url or (f"https://t.me/{username}" if username else ""),
        domain="t.me",
        source_type=source_type,
        username=username,
    )


def test_probe_telegram_available() -> None:
    body = (Path(__file__).parent.parent / "fixtures" / "telegram" / "tg_preview_extremizmunet.html").read_text(
        encoding="utf-8"
    )
    resp = HttpResponse(status=200, text=body, url="https://t.me/s/extremizmunet", health=FetchHealth.ok)
    result = probe_source(_entry(username="extremizmunet"), client=_FakeClient(response=resp))
    assert result.status == AVAILABLE
    assert result.http_status == 200
    assert "постов" in result.note


def test_probe_timeout_is_temporarily_unavailable() -> None:
    result = probe_source(
        _entry(username="abc"),
        client=_FakeClient(exc=httpx.ConnectTimeout("timed out")),
    )
    assert result.status == TEMPORARILY_UNAVAILABLE
    assert result.http_status == 0
    # Never classified as removed/dead.
    assert "тайм" in result.note.lower() or "network" in result.note.lower() or "dns" in result.note.lower()


def test_probe_dns_failure_does_not_raise() -> None:
    result = probe_source(
        _entry(username="abc"),
        client=_FakeClient(exc=httpx.ConnectError("name resolution failed")),
    )
    assert result.status == TEMPORARILY_UNAVAILABLE


def test_probe_unsupported_source_type() -> None:
    result = probe_source(_entry(source_type="website", username=None, url="https://x.io"))
    assert result.status == UNSUPPORTED


def test_probe_invalid_telegram_username() -> None:
    entry = SourceRegistryEntry(
        id="bad", name="bad", url="", domain="", source_type="telegram", username=None
    )
    result = probe_source(entry)
    assert result.status == INVALID_URL
