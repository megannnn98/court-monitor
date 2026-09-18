"""The wiki as one PDF (`/ui/wiki/export.pdf`) and the button that downloads it."""

from __future__ import annotations

import re
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

import api
from api import _wiki_pdf_html, app, get_db

BUTTON = 'href="/ui/wiki/export.pdf"'


@pytest.fixture
def wiki(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A small wiki in place of docs/wiki, with a diagram, and PlantUML stubbed."""
    (tmp_path / "Home.md").write_text("# court-monitor — вики\n\nНачало.\n", encoding="utf-8")
    (tmp_path / "Architecture.md").write_text(
        "# Architecture\n\n```plantuml\n@startuml\nA -> B\n@enduml\n```\n", encoding="utf-8"
    )
    (tmp_path / "Setup.md").write_text(
        "# Setup\n\n| a | b |\n|---|---|\n| 1 | 2 |\n", encoding="utf-8"
    )
    monkeypatch.setattr(api, "_wiki_root", lambda: tmp_path)
    monkeypatch.setattr("api.shutil.which", lambda name: "/usr/bin/plantuml")
    monkeypatch.setattr(
        "api.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=["plantuml"],
            returncode=0,
            stdout='<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"></svg>',
            stderr="",
        ),
    )
    return tmp_path


@pytest.fixture
def client(session_factory: sessionmaker[Session]) -> Iterator[TestClient]:
    def override_get_db() -> Iterator[Session]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_the_document_holds_every_page_home_first_each_from_a_new_sheet(wiki: Path) -> None:
    document = _wiki_pdf_html()

    contents = re.findall(r'<li><a href="#wiki-([^"]+)">', document)
    assert contents == ["Home", "Architecture", "Setup"]
    sections = re.findall(r'<section class="wiki-page" id="wiki-([^"]+)">', document)
    assert sections == contents
    assert ".wiki-page { break-before: page; }" in document
    # The diagram is the site's SVG, and it is scaled to fit one sheet.
    assert '<figure class="wiki-diagram"><svg' in document
    assert "max-height: 220mm" in document


def test_the_export_is_a_downloadable_pdf(wiki: Path, client: TestClient) -> None:
    response = client.get("/ui/wiki/export.pdf")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"] == (
        'attachment; filename="court-monitor-wiki.pdf"'
    )
    assert response.content.startswith(b"%PDF-")


def test_the_button_is_on_the_wiki_home_and_contents_only(wiki: Path, client: TestClient) -> None:
    assert BUTTON in client.get("/ui/wiki").text
    assert BUTTON in client.get("/ui/wiki/Home").text
    assert BUTTON not in client.get("/ui/wiki/Setup").text
