"""A demo stand serves reads alone (ADR 0029)."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from web.app import app
from web.demo import read_only, refused_by_demo
from web.dependencies import get_db


@pytest.mark.parametrize(
    ("method", "refused"),
    [
        ("GET", False),
        ("head", False),
        ("OPTIONS", False),
        # An investigation too: the console has no page that starts one.
        ("POST", True),
        ("PUT", True),
        ("DELETE", True),
        ("PATCH", True),
    ],
)
def test_what_a_demo_refuses(method: str, refused: bool) -> None:
    assert refused_by_demo(method) is refused


@pytest.mark.parametrize(
    ("value", "enabled"), [("1", True), (" 1 ", True), ("0", False), ("", False), ("yes", False)]
)
def test_only_one_turns_the_demo_on(
    monkeypatch: pytest.MonkeyPatch, value: str, enabled: bool
) -> None:
    monkeypatch.setenv("DEMO_READ_ONLY", value)

    assert read_only() is enabled


def test_a_demo_refuses_a_change_before_the_route_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    """The purge would reach the database; the refusal comes first, so none is needed."""
    monkeypatch.setenv("DEMO_READ_ONLY", "1")

    refused = TestClient(app).post("/ui/management/purge", data={})

    assert refused.status_code == 403
    assert refused.json()["error"]["code"] == "demo_read_only"


def test_without_the_flag_nothing_is_refused_as_a_demo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DEMO_READ_ONLY", raising=False)

    # An address no route answers: nothing reaches a database either way.
    response = TestClient(app).post("/api/v1/no-such-change", json={})

    assert response.status_code != 403
    assert "demo_read_only" not in response.text


@contextmanager
def _client(session_factory: sessionmaker[Session]) -> Iterator[TestClient]:
    def override() -> Iterator[Session]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_the_status_tells_the_console_it_is_a_demo(
    session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    with _client(session_factory) as client:
        monkeypatch.delenv("DEMO_READ_ONLY", raising=False)
        assert client.get("/api/v1/status").json()["read_only"] is False

        monkeypatch.setenv("DEMO_READ_ONLY", "1")
        assert client.get("/api/v1/status").json()["read_only"] is True
