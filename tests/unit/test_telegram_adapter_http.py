"""Tests: TelegramChannelAdapter live mode — blocked/erroring responses (D-011).

Mirrors test_sudrf_http_adapter.py: a live fetch that returns no usable
content must surface as a FetchProblem so the service layer can raise a
ReviewItem, instead of silently vanishing.
"""

from __future__ import annotations

import court_monitor.sources.telegram_channel as telegram_module
from court_monitor.domain.models import FetchHealth
from court_monitor.sources.base import FetchProblem, FetchResult
from court_monitor.sources.http_client import HttpResponse
from court_monitor.sources.telegram_channel import TelegramChannelAdapter

PREVIEW_HTML = (
    '<div class="tgme_widget_message_wrap">'
    '<div class="tgme_widget_message" data-post="demo/1">'
    '<div class="tgme_widget_message_text">Hello</div>'
    '<a class="tgme_widget_message_date" href="https://t.me/demo/1">'
    '<time datetime="2026-01-01T00:00:00+00:00"></time></a>'
    "</div></div>"
)


class _FakeClient:
    def __init__(self, response: HttpResponse) -> None:
        self._response = response

    def get(self, url: str) -> HttpResponse:  # noqa: ARG002
        return self._response

    def __enter__(self) -> _FakeClient:
        return self

    def __exit__(self, *exc: object) -> None:
        pass


def _adapter() -> TelegramChannelAdapter:
    return TelegramChannelAdapter(
        source_id="demo", name="Demo Channel", username="demo", url="https://t.me/demo"
    )


def _install_fake_client(monkeypatch, response: HttpResponse) -> None:
    monkeypatch.setattr(telegram_module, "HttpClient", lambda: _FakeClient(response))


def test_live_ok_response_yields_fetch_results(monkeypatch):
    resp = HttpResponse(
        status=200, text=PREVIEW_HTML, url="https://t.me/s/demo", health=FetchHealth.ok
    )
    _install_fake_client(monkeypatch, resp)

    results = list(_adapter().fetch_new(live=True))

    assert len(results) == 1
    assert isinstance(results[0], FetchResult)


def test_live_blocked_response_yields_fetch_problem(monkeypatch):
    resp = HttpResponse(
        status=403, text="captcha", url="https://t.me/s/demo", health=FetchHealth.blocked
    )
    _install_fake_client(monkeypatch, resp)

    results = list(_adapter().fetch_new(live=True))

    assert len(results) == 1
    problem = results[0]
    assert isinstance(problem, FetchProblem)
    assert problem.health == FetchHealth.blocked
    assert problem.http_status == 403
    assert problem.source_id == "demo"


def test_live_http_error_yields_fetch_problem(monkeypatch):
    resp = HttpResponse(
        status=500, text="", url="https://t.me/s/demo", health=FetchHealth.http_error
    )
    _install_fake_client(monkeypatch, resp)

    results = list(_adapter().fetch_new(live=True))

    assert len(results) == 1
    assert isinstance(results[0], FetchProblem)
    assert results[0].health == FetchHealth.http_error


def test_live_empty_body_yields_fetch_problem(monkeypatch):
    resp = HttpResponse(status=200, text="", url="https://t.me/s/demo", health=FetchHealth.ok)
    _install_fake_client(monkeypatch, resp)

    results = list(_adapter().fetch_new(live=True))

    assert len(results) == 1
    assert isinstance(results[0], FetchProblem)


def test_live_not_modified_is_silently_skipped(monkeypatch):
    resp = HttpResponse(
        status=304, text="", url="https://t.me/s/demo", health=FetchHealth.not_modified
    )
    _install_fake_client(monkeypatch, resp)

    results = list(_adapter().fetch_new(live=True))

    assert results == []


def test_fixture_missing_does_not_yield_fetch_problem():
    """Local fixture-missing is a dev/test setup issue, not a production
    source problem — must NOT create a ReviewItem."""
    adapter = TelegramChannelAdapter(
        source_id="demo",
        name="Demo Channel",
        username="demo",
        url="https://t.me/demo",
        fixture_path="tests/fixtures/telegram/does-not-exist.html",
    )

    results = list(adapter.fetch_new(live=False))

    assert results == []
