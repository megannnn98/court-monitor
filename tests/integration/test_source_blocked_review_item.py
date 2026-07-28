"""Integration test: a blocked/erroring source fetch raises a ReviewItem (D-011).

Covers both call sites that consume FetchProblem: process_source (SudrfAdapter,
config/sources.yaml-style) and process_registry_source (TelegramChannelAdapter,
registry-style). No network: HttpClient is monkeypatched.
"""

from __future__ import annotations

import court_monitor.sources.sudrf as sudrf_module
import court_monitor.sources.telegram_channel as telegram_module
from court_monitor.config.loader import SourceConfig
from court_monitor.config.registry import SourceRegistryEntry
from court_monitor.domain.models import FetchHealth, SourceBackend, SourceType
from court_monitor.services import process_registry_source, process_source
from court_monitor.sources.http_client import HttpResponse
from court_monitor.storage import repository as repo


class _FakeClient:
    def __init__(self, response: HttpResponse) -> None:
        self._response = response

    def get(self, url: str) -> HttpResponse:  # noqa: ARG002
        return self._response

    def __enter__(self) -> _FakeClient:
        return self

    def __exit__(self, *exc: object) -> None:
        pass


def _sudrf_source() -> SourceConfig:
    return SourceConfig(
        name="test-court",
        type=SourceType.sudrf,
        backend=SourceBackend.http,
        base_url="https://test-court.example",
        paths=("/press",),
    )


def _telegram_entry() -> SourceRegistryEntry:
    return SourceRegistryEntry(
        id="demo",
        name="Demo Channel",
        url="https://t.me/demo",
        domain="t.me",
        source_type="telegram",
        username="demo",
    )


def test_process_source_blocked_creates_review_item(db_session, monitoring_cfg, monkeypatch):
    resp = HttpResponse(
        status=403,
        text="captcha",
        url="https://test-court.example/press",
        health=FetchHealth.blocked,
    )
    monkeypatch.setattr(sudrf_module, "HttpClient", lambda: _FakeClient(resp))

    stats = process_source(db_session, _sudrf_source(), monitoring_cfg)

    assert stats.fetched == 1
    assert stats.blocked == 1
    assert stats.new_documents == 0
    assert repo.count_documents(db_session) == 0

    items = repo.list_review_items(db_session, item_type="source_blocked")
    assert len(items) == 1
    assert items[0].source_id == "test-court"
    assert items[0].priority == "high"
    assert "blocked" in items[0].data_json


def test_process_source_blocked_repeated_runs_do_not_duplicate(
    db_session, monitoring_cfg, monkeypatch
):
    resp = HttpResponse(
        status=403,
        text="captcha",
        url="https://test-court.example/press",
        health=FetchHealth.blocked,
    )
    monkeypatch.setattr(sudrf_module, "HttpClient", lambda: _FakeClient(resp))

    process_source(db_session, _sudrf_source(), monitoring_cfg)
    process_source(db_session, _sudrf_source(), monitoring_cfg)

    items = repo.list_review_items(db_session, item_type="source_blocked")
    assert len(items) == 1


def test_process_registry_source_blocked_creates_review_item(
    db_session, monitoring_cfg, monkeypatch
):
    resp = HttpResponse(
        status=500, text="", url="https://t.me/s/demo", health=FetchHealth.http_error
    )
    monkeypatch.setattr(telegram_module, "HttpClient", lambda: _FakeClient(resp))

    stats = process_registry_source(db_session, _telegram_entry(), monitoring_cfg, live=True)

    assert stats.fetched == 1
    assert stats.blocked == 1
    assert stats.new_documents == 0

    items = repo.list_review_items(db_session, item_type="source_blocked")
    assert len(items) == 1
    assert items[0].source_id == "demo"


def test_process_source_ok_does_not_create_review_item(db_session, monitoring_cfg, monkeypatch):
    """Sanity check: a healthy fetch must not spuriously raise a ReviewItem."""
    resp = HttpResponse(
        status=200,
        text="<html><body>ст. 205.1 УК РФ</body></html>",
        url="https://test-court.example/press",
        health=FetchHealth.ok,
    )
    monkeypatch.setattr(sudrf_module, "HttpClient", lambda: _FakeClient(resp))

    stats = process_source(db_session, _sudrf_source(), monitoring_cfg)

    assert stats.blocked == 0
    assert repo.count_review_items(db_session) == 0
