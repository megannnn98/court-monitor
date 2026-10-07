"""An unsafe request is served only from the console's own origin (ADR 0022, option A)."""

from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient

from web.app import app
from web.csrf import DEFAULT_ALLOWED_ORIGINS, allowed_origins, refused_origin

ALLOWED = frozenset(DEFAULT_ALLOWED_ORIGINS)
HOST = "127.0.0.1:8001"


@pytest.mark.parametrize(
    ("method", "headers", "refused"),
    [
        # A safe method is never refused, whoever asks.
        ("GET", {"origin": "https://evil.example"}, None),
        ("HEAD", {"origin": "https://evil.example"}, None),
        # The browser says it is the console's own page.
        ("POST", {"sec-fetch-site": "same-origin", "origin": "https://evil.example"}, None),
        # The console's own origin, by the host the request came to.
        ("POST", {"origin": "http://127.0.0.1:8001"}, None),
        # The Vite dev server proxies to the API: its origin is on the list.
        ("POST", {"origin": "http://localhost:5173"}, None),
        # Another site.
        ("POST", {"origin": "https://evil.example"}, "https://evil.example"),
        (
            "POST",
            {"sec-fetch-site": "cross-site", "origin": "https://evil.example"},
            "https://evil.example",
        ),
        ("DELETE", {"origin": "https://evil.example"}, "https://evil.example"),
        # A sandboxed frame or a data: page says «null».
        ("POST", {"origin": "null"}, "null"),
        # No Origin: the Referer tells instead.
        ("POST", {"referer": "https://evil.example/page?x=1"}, "https://evil.example"),
        ("POST", {"referer": "http://127.0.0.1:8001/ui/political"}, None),
        # Not a browser: no forgery without one.
        ("POST", {}, None),
    ],
)
def test_who_may_send_an_unsafe_request(
    method: str, headers: dict[str, str], refused: str | None
) -> None:
    assert refused_origin(method, HOST, headers, ALLOWED) == refused


def test_a_deployment_names_its_own_origins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALLOWED_ORIGINS", " https://console.example/ , http://10.0.0.5:8001 ")

    assert allowed_origins() == {"https://console.example", "http://10.0.0.5:8001"}
    assert (
        refused_origin("POST", HOST, {"origin": "https://console.example"}, allowed_origins())
        is None
    )
    # The list replaces the default one.
    assert refused_origin("POST", HOST, {"origin": "http://localhost:5173"}, allowed_origins()) == (
        "http://localhost:5173"
    )


def test_the_app_refuses_a_cross_site_form_before_the_route_runs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The purge is irreversible: a foreign page must not reach it, with or without a
    database — the refusal comes before the route."""
    client = TestClient(app)

    with caplog.at_level(logging.WARNING, logger="api"):
        refused = client.post(
            "/ui/management/purge", data={}, headers={"Origin": "https://evil.example"}
        )

    assert refused.status_code == 403
    body = refused.json()
    assert body["error"]["code"] == "csrf_refused"
    assert body["error"]["message"] == "cross-origin request refused: https://evil.example"
    assert refused.headers["X-Request-ID"] == body["error"]["request_id"]
    assert any(
        "event=csrf_refused" in record.getMessage()
        and "/ui/management/purge" in record.getMessage()
        for record in caplog.records
    )


def test_the_app_lets_its_own_pages_and_reads_through() -> None:
    client = TestClient(app)

    # A read is never refused; an own-origin POST reaches the route (which answers as it
    # does, here without a database or a form — anything but the refusal).
    assert client.get("/health/live", headers={"Origin": "https://evil.example"}).status_code == 200
    own = client.post("/ui/political/done", data={}, headers={"Origin": "http://testserver"})
    assert own.status_code != 403 or own.json().get("error", {}).get("code") != "csrf_refused"
