from __future__ import annotations

import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from support.person_resolution_fixtures import seed_mentions, seed_person
from support.research_db_fixtures import ResearchSeeder

from api import app, get_db, get_operation_registry
from db.orm_models import (
    EntityMentionRecord,
    EventEntityMentionRecord,
    ExtractedEventRecord,
    PersonResolutionDecisionRecord,
)
from operator_console import OperationRegistry
from persons.resolution.factory import build_person_resolution_service


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


def _pending_review(session_factory: sessionmaker[Session], surface: str) -> int:
    run_id, (mention_id,) = seed_mentions(session_factory, surface)
    with session_factory.begin() as session:
        event = ExtractedEventRecord(
            extraction_run_id=run_id,
            event_type="detention",
            start_offset=0,
            end_offset=len(surface),
            confidence=0.8,
            attributes={},
            extractor_name="test",
            extractor_version="1",
        )
        session.add(event)
        session.flush()
        session.add(
            EventEntityMentionRecord(event_id=event.id, mention_id=mention_id, role="subject")
        )
    service = build_person_resolution_service(session_factory, {})
    with session_factory.begin() as session:
        outcome = service.resolve_mention(session, session.get_one(EntityMentionRecord, mention_id))
    assert outcome is not None and outcome.decision_id is not None
    return outcome.decision_id


def test_local_ui_review_applies_decision_and_moves_to_next(
    session_factory: sessionmaker[Session],
) -> None:
    ivan = seed_person(session_factory, "Иван Иванов")
    seed_person(session_factory, "Илья Иванов")
    first = _pending_review(session_factory, "И. Иванов")
    second = _pending_review(session_factory, "И. Иванов")

    with _client(session_factory) as client:
        page = client.get(f"/ui/person-resolution/reviews/{first}")
        applied = client.post(
            f"/ui/person-resolution/reviews/{first}/decision",
            params={"action": "link_to_person", "person_id": ivan},
            follow_redirects=False,
        )

    assert page.status_code == 200
    assert "И. Иванов" in page.text
    assert applied.status_code == 303
    assert applied.headers["location"] == f"/ui/person-resolution/reviews/{second}"
    with session_factory() as session:
        statuses = {
            row.id: row.status for row in session.scalars(select(PersonResolutionDecisionRecord))
        }
    assert statuses[first] == "reviewed"
    assert statuses[second] == "pending_review"


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

    assert detail.status_code == 200
    body = detail.json()
    assert body["person"]["canonical_name"] == "Иван Иванов"
    assert body["persecution"]["status"] == "political"
    assert body["rosfinmonitoring"]["status"] == "not_matched"
    assert body["events"][0]["evidence"]["text"] == "Суд назначил штраф Ивану Иванову"
    assert ui_person.status_code == 200
    assert "Суд назначил штраф Ивану Иванову" in ui_person.text
    assert article.json()["text"].startswith("Иван Иванов участвовал")
    assert [hit["article_id"] for hit in search.json()] == [article_id]


def test_ui_pages_have_operator_shell_and_contextual_instruction(
    session_factory: sessionmaker[Session],
) -> None:
    with _client(session_factory) as client:
        review = client.get("/ui/person-resolution/reviews")
        search = client.get("/ui/search")
        operations = client.get("/ui/operations")
        monitoring = client.get("/ui/monitoring")

    for response in (review, search, operations, monitoring):
        assert response.status_code == 200
        assert "court-monitor" in response.text
        assert "Дальше:" in response.text
        assert "ER pending" in response.text


def test_operation_preview_confirm_and_run_detail_use_background_registry(
    session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = OperationRegistry()

    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args=["fake"], returncode=0, stdout="done\n", stderr="")

    monkeypatch.setattr("operator_console.subprocess.run", fake_run)

    def override_registry() -> OperationRegistry:
        return registry

    app.dependency_overrides[get_operation_registry] = override_registry
    try:
        with _client(session_factory) as client:
            preview = client.get(
                "/ui/operations/discover-and-ingest",
                params={"source": "ovd-info", "limit": 3},
            )
            started = client.post(
                "/ui/operations/discover-and-ingest/confirm",
                params={"source": "ovd-info", "limit": 3},
                follow_redirects=False,
            )
            run = client.get(started.headers["location"])
            api_run = client.get("/operations/runs/1")
    finally:
        app.dependency_overrides.pop(get_operation_registry, None)

    assert preview.status_code == 200
    assert "Preview" in preview.text
    assert "Операция ходит в сеть" in preview.text
    assert started.status_code == 303
    assert started.headers["location"] == "/ui/operations/runs/1"
    assert run.status_code == 200
    assert "Run #1" in run.text
    assert "done" in run.text
    assert api_run.json()["parameters"] == {"source": "ovd-info", "limit": 3, "workers": None}
