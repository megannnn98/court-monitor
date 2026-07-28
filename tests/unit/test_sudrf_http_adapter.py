"""Tests: SudrfAdapter http backend — blocked/erroring responses (D-011).

A fetch attempt that returns no usable content must surface as a
FetchProblem, not silently vanish — the service layer turns that into a
ReviewItem so an operator sees a blocked/erroring source.
"""

from __future__ import annotations

import court_monitor.sources.sudrf as sudrf_module
from court_monitor.config.loader import SourceConfig
from court_monitor.domain.models import FetchHealth, SourceBackend, SourceType
from court_monitor.sources.base import FetchProblem, FetchResult
from court_monitor.sources.http_client import HttpResponse
from court_monitor.sources.sudrf import SudrfAdapter


class _FakeClient:
    """Stand-in for HttpClient returning canned responses in order."""

    def __init__(self, responses: list[HttpResponse]) -> None:
        self._responses = iter(responses)

    def get(self, url: str) -> HttpResponse:  # noqa: ARG002
        return next(self._responses)

    def __enter__(self) -> _FakeClient:
        return self

    def __exit__(self, *exc: object) -> None:
        pass


def _config(paths: tuple[str, ...] = ("/a",)) -> SourceConfig:
    return SourceConfig(
        name="test-court",
        type=SourceType.sudrf,
        backend=SourceBackend.http,
        base_url="https://test-court.example",
        paths=paths,
    )


def _install_fake_client(monkeypatch, responses: list[HttpResponse]) -> None:
    monkeypatch.setattr(sudrf_module, "HttpClient", lambda: _FakeClient(responses))


def test_ok_response_yields_fetch_result(monkeypatch):
    resp = HttpResponse(
        status=200,
        text="<html>content</html>",
        url="https://test-court.example/a",
        health=FetchHealth.ok,
    )
    _install_fake_client(monkeypatch, [resp])

    results = list(SudrfAdapter(_config()).fetch_new())

    assert len(results) == 1
    assert isinstance(results[0], FetchResult)


def test_blocked_response_yields_fetch_problem(monkeypatch):
    resp = HttpResponse(
        status=403,
        text="captcha check",
        url="https://test-court.example/a",
        health=FetchHealth.blocked,
    )
    _install_fake_client(monkeypatch, [resp])

    results = list(SudrfAdapter(_config()).fetch_new())

    assert len(results) == 1
    problem = results[0]
    assert isinstance(problem, FetchProblem)
    assert problem.health == FetchHealth.blocked
    assert problem.http_status == 403
    assert problem.source_id == "test-court"
    assert problem.url == "https://test-court.example/a"


def test_http_error_yields_fetch_problem(monkeypatch):
    resp = HttpResponse(
        status=500, text="", url="https://test-court.example/a", health=FetchHealth.http_error
    )
    _install_fake_client(monkeypatch, [resp])

    results = list(SudrfAdapter(_config()).fetch_new())

    assert len(results) == 1
    assert isinstance(results[0], FetchProblem)
    assert results[0].health == FetchHealth.http_error


def test_timeout_yields_fetch_problem(monkeypatch):
    resp = HttpResponse(
        status=0, text="", url="https://test-court.example/a", health=FetchHealth.timeout
    )
    _install_fake_client(monkeypatch, [resp])

    results = list(SudrfAdapter(_config()).fetch_new())

    assert len(results) == 1
    assert isinstance(results[0], FetchProblem)
    assert results[0].health == FetchHealth.timeout


def test_ok_but_empty_body_yields_fetch_problem(monkeypatch):
    """Status 200 with no body is not usable content either."""
    resp = HttpResponse(
        status=200, text="", url="https://test-court.example/a", health=FetchHealth.ok
    )
    _install_fake_client(monkeypatch, [resp])

    results = list(SudrfAdapter(_config()).fetch_new())

    assert len(results) == 1
    assert isinstance(results[0], FetchProblem)
    assert results[0].health == FetchHealth.ok


def test_not_modified_is_silently_skipped_not_a_problem(monkeypatch):
    """304 means "no new content" — a normal outcome, not a failure."""
    resp = HttpResponse(
        status=304, text="", url="https://test-court.example/a", health=FetchHealth.not_modified
    )
    _install_fake_client(monkeypatch, [resp])

    results = list(SudrfAdapter(_config()).fetch_new())

    assert results == []


def test_mixed_paths_yield_both_result_and_problem(monkeypatch):
    ok_resp = HttpResponse(
        status=200,
        text="<html>ok</html>",
        url="https://test-court.example/a",
        health=FetchHealth.ok,
    )
    blocked_resp = HttpResponse(
        status=403, text="captcha", url="https://test-court.example/b", health=FetchHealth.blocked
    )
    _install_fake_client(monkeypatch, [ok_resp, blocked_resp])

    results = list(SudrfAdapter(_config(paths=("/a", "/b"))).fetch_new())

    assert len(results) == 2
    assert isinstance(results[0], FetchResult)
    assert isinstance(results[1], FetchProblem)
