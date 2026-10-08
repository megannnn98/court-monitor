"""The API serves the React console when the image carries its build (`web.spa`)."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import unquote

import pytest
from fastapi.testclient import TestClient

from web.app import app
from web.spa import react_address


@pytest.fixture
def built(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "dist"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text("<div id=root></div>", encoding="utf-8")
    (root / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    # Beside the build, never served.
    (tmp_path / "secret.txt").write_text("secret", encoding="utf-8")
    monkeypatch.setenv("FRONTEND_DIST", str(root))
    return root


def test_every_console_address_is_the_page_and_a_file_is_the_file(built: Path) -> None:
    client = TestClient(app)

    for address in ("/work", "/political?queue=done", "/investigations/анна смирнова"):
        response = client.get(address)
        assert response.status_code == 200 and "<div id=root>" in response.text, address
    assert client.get("/assets/app.js").text == "console.log(1)"
    # Out of the build: the page, not a file elsewhere on the disk.
    assert "<div id=root>" in client.get("/assets/..%2F..%2Fsecret.txt").text
    # A missing API route stays a 404, never the page.
    for address in ("/api/v1/nothing", "/static/nothing.js", "/health/nothing"):
        assert client.get(address).status_code == 404, address


def test_a_moved_legacy_page_opens_in_react_with_its_query(built: Path) -> None:
    client = TestClient(app, follow_redirects=False)

    for legacy, react in (
        ("/", "/work"),
        ("/ui/cycle", "/work"),
        ("/ui/political?months=3", "/political?months=3"),
        ("/ui/entities?region=Москва", "/entities?region=Москва"),
        ("/ui/investigations/анна%20смирнова", "/investigations/анна смирнова"),
        ("/ui/articles/12", "/articles/12"),
        ("/ui/persons/5", "/persons/5"),
    ):
        response = client.get(legacy)
        assert response.status_code == 302, legacy
        assert unquote(response.headers["location"]) == react, legacy
    # Not moved yet: the legacy page; a file and a form are never sent away.
    assert react_address("/ui/unnamed") is None
    assert react_address("/ui/people/export.xlsx") is None
    assert react_address("/ui/investigations/a/graph") is None
    assert client.post("/ui/cycle").status_code != 302


def test_without_the_build_the_legacy_pages_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FRONTEND_DIST", str(tmp_path))
    client = TestClient(app, follow_redirects=False)

    assert client.get("/ui/cycle", follow_redirects=False).status_code != 302
    assert client.get("/work").status_code == 404
