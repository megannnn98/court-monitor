from __future__ import annotations

import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker
from support.research_db_fixtures import ResearchSeeder

from api import app, get_db
from web.wiki import _wiki_markdown_to_html


@contextmanager
def _client(session_factory: sessionmaker[Session]) -> Iterator[TestClient]:
    def override_get_db() -> Iterator[Session]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_person_detail_and_article_routes_expose_evidence_span(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        seed = ResearchSeeder(session)
        source_id = seed.source("test-source", "https://source.test")
        article_id, run_id = seed.article(
            source_id,
            external_id="evidence-1",
            title="Приговор по делу",
            text="Иван Иванов участвовал в пикете. Суд назначил штраф Ивану Иванову.",
        )
        person_id = seed.person("Иван Иванов")
        mention_id = seed.mention(run_id, "Иван Иванов", person_id=person_id)
        seed.event(
            run_id,
            "Суд назначил штраф Ивану Иванову",
            event_type="fine",
            event_date=datetime(2026, 9, 16, tzinfo=UTC),
            links=[(person_id, "subject")],
            entity_links=[(mention_id, "subject")],
        )
        seed.classification(person_id, "political", 0.91, reasons=["anti_war"])
        snapshot_id = seed.snapshot()
        seed.match(person_id, snapshot_id, "not_matched", 0.8)
        session.commit()

    with _client(session_factory) as client:
        detail = client.get(f"/persons/{person_id}/detail")
        ui_person = client.get(f"/ui/persons/{person_id}")
        article = client.get(f"/articles/{article_id}")
        search = client.get("/search/articles", params={"query": "пикет"})
        # The seeded news is dated 2024: no period filter.
        # The seeded news is a fine without a criminal-code charge: an administrative case,
        # hidden by default (48c4851), so the page is asked for administrative cases too.
        filters = f"snapshot_id={snapshot_id}&date_from=&include_administrative=1"
        candidates_page = client.get(f"/ui/candidates?{filters}")
        exported = client.get(f"/ui/candidates/export?{filters}")
        exported_pdf = client.get(f"/ui/candidates/export.pdf?{filters}")

    assert detail.status_code == 200
    body = detail.json()
    assert body["person"]["canonical_name"] == "Иван Иванов"
    assert body["persecution"]["status"] == "political"
    assert body["rosfinmonitoring"]["status"] == "not_matched"
    assert body["events"][0]["evidence"]["text"] == "Суд назначил штраф Ивану Иванову"
    assert ui_person.status_code == 200
    assert "Суд назначил штраф Ивану Иванову" in ui_person.text
    # The card's heading writes the name surname first, as the tables do.
    assert "<title>Иванов Иван</title>" in ui_person.text
    assert article.json()["text"].startswith("Иван Иванов участвовал")
    assert [hit["article_id"] for hit in search.json()] == [article_id]
    assert candidates_page.status_code == 200
    assert "<th>№</th>" in candidates_page.text
    assert "<td>1</td>" in candidates_page.text
    assert exported.status_code == 200
    assert exported.headers["content-type"].startswith("text/csv")
    assert "Иван Иванов" in exported.text
    assert "not_matched" in exported.text
    assert exported_pdf.status_code == 200
    assert exported_pdf.headers["content-type"] == "application/pdf"
    assert exported_pdf.content.startswith(b"%PDF-")


def test_ui_pages_have_operator_shell_and_contextual_instruction(
    session_factory: sessionmaker[Session],
) -> None:
    with _client(session_factory) as client:
        candidates = client.get("/ui/candidates")
        wiki = client.get("/ui/wiki")
        wiki_page = client.get("/ui/wiki/Local-Web-UI")

    for response in (candidates, wiki, wiki_page):
        assert response.status_code == 200
        assert "court-monitor" in response.text
        assert "Дальше:" in response.text
    assert "Local-Web-UI" in wiki.text


def test_wiki_renders_plantuml_as_svg(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("web.wiki.shutil.which", lambda name: "/usr/bin/plantuml")
    monkeypatch.setattr(
        "web.wiki.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=["plantuml"], returncode=0, stdout="<svg>diagram</svg>", stderr=""
        ),
    )

    markdown = "```plantuml\n@startuml\nAlice -> Bob\n@enduml\n```"

    rendered = _wiki_markdown_to_html(markdown)

    assert '<figure class="wiki-diagram"><svg>diagram</svg></figure>' in rendered
