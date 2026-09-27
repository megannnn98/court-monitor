"""Tests for FastAPI application."""

import json
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker
from support.research_db_fixtures import ResearchSeeder

from api import _get_session_factory, app, get_db


def test_api_health_check() -> None:
    """Test health check endpoint."""
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_api_has_persons_endpoint() -> None:
    """Test that persons endpoint exists."""
    client = TestClient(app)
    # Just check the endpoint exists (will fail due to no DB, but that's OK)
    response = client.get("/persons")
    # Should get 503 due to no DATABASE_URL, not 404
    assert response.status_code in (200, 503)


def test_api_has_candidates_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that candidates endpoint exists."""
    # Independent of an ambient DATABASE_URL: with a real database and no
    # snapshot 1 the endpoint answers 404, which is indistinguishable from a
    # missing route.
    monkeypatch.delenv("DATABASE_URL", raising=False)
    _get_session_factory.cache_clear()
    try:
        response = TestClient(app).get("/candidates?snapshot_id=1")
    finally:
        _get_session_factory.cache_clear()
    # 503 (no DATABASE_URL) proves the route exists; a missing route is 404.
    assert response.status_code == 503


def test_api_has_rosfinmonitoring_snapshots_endpoint() -> None:
    """Test that rosfinmonitoring snapshots endpoint exists."""
    client = TestClient(app)
    response = client.get("/rosfinmonitoring/snapshots")
    # Should get 503 due to no DATABASE_URL, not 404
    assert response.status_code in (200, 503)


def test_api_has_reviews_endpoint() -> None:
    """Test that reviews endpoint exists."""
    client = TestClient(app)
    response = client.get("/reviews")
    # Should get 503 due to no DATABASE_URL, not 404
    assert response.status_code in (200, 503)


def test_api_missing_database_url_returns_503(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that missing DATABASE_URL is a controlled service error."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    _get_session_factory.cache_clear()
    client = TestClient(app)

    response = client.get("/persons")

    assert response.status_code == 503
    assert response.json()["detail"] == "DATABASE_URL environment variable is not set"


@pytest.fixture
def db_client(session_factory: sessionmaker[Session]) -> Iterator[TestClient]:
    def override_get_db() -> Iterator[Session]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.mark.parametrize("stale_inserted_first", [True, False])
def test_person_persecution_returns_latest_classification(
    session_factory: sessionmaker[Session], db_client: TestClient, stale_inserted_first: bool
) -> None:
    stale = ("political", 0.9, "1.0.0", datetime(2024, 1, 1, tzinfo=UTC))
    latest = ("non_political", 0.95, "2.0.0", datetime(2024, 6, 1, tzinfo=UTC))
    with session_factory() as session:
        seed = ResearchSeeder(session)
        person_id = seed.person("Иван Иванов")
        # Insertion order must not decide the answer (an unordered read
        # usually follows it).
        for status, confidence, version, classified_at in (
            (stale, latest) if stale_inserted_first else (latest, stale)
        ):
            seed.classification(
                person_id,
                status,
                confidence,
                classifier_version=version,
                classified_at=classified_at,
            )
        session.commit()

    response = db_client.get(f"/persons/{person_id}/persecution")

    assert response.status_code == 200
    body = response.json()
    assert (body["status"], body["classifier_version"]) == ("non_political", "2.0.0")


def test_person_persecution_is_null_without_classification(
    session_factory: sessionmaker[Session], db_client: TestClient
) -> None:
    with session_factory() as session:
        person_id = ResearchSeeder(session).person("Иван Иванов")
        session.commit()

    response = db_client.get(f"/persons/{person_id}/persecution")

    assert response.status_code == 200
    assert response.json() is None


def _pending_person_resolution(session_factory: sessionmaker[Session]) -> tuple[int, int]:
    from support.person_resolution_fixtures import seed_mentions, seed_person

    from db.orm_models import EntityMentionRecord
    from persons.resolution.factory import build_person_resolution_service

    ivan = seed_person(session_factory, "Иван Иванов")
    seed_person(session_factory, "Илья Иванов")
    _, (mention_id,) = seed_mentions(session_factory, "И. Иванов")
    service = build_person_resolution_service(session_factory, {})
    with session_factory.begin() as session:
        outcome = service.resolve_mention(session, session.get_one(EntityMentionRecord, mention_id))
    assert outcome is not None and outcome.decision_id is not None
    return outcome.decision_id, ivan


def test_person_resolution_review_api_shows_and_applies_a_decision(
    db_client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    decision_id, ivan = _pending_person_resolution(session_factory)

    listed = db_client.get("/person-resolution/reviews").json()
    shown = db_client.get(f"/person-resolution/reviews/{decision_id}").json()
    applied = db_client.post(
        f"/person-resolution/reviews/{decision_id}/decision",
        json={"action": "link_to_person", "person_id": ivan, "note": "same case"},
    )
    again = db_client.post(
        f"/person-resolution/reviews/{decision_id}/decision",
        json={"action": "link_to_person", "person_id": ivan},
    )

    assert [item["decision_id"] for item in listed] == [decision_id]
    assert shown["incoming_name"] == "И. Иванов"
    candidate = next(c for c in shown["candidates"] if c["person_id"] == ivan)
    assert candidate["surname"] == {"match": "exact", "similarity": 1.0}
    assert candidate["given_name"]["match"] == "initial_compatible"
    assert "probability" not in json.dumps(shown)
    assert applied.status_code == 200 and applied.json()["person_id"] == ivan
    assert again.status_code == 409
    assert db_client.get("/person-resolution/reviews").json() == []


def test_person_resolution_review_api_errors(
    db_client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    decision_id, _ = _pending_person_resolution(session_factory)

    assert db_client.get("/person-resolution/reviews/999999").status_code == 404
    missing_person = db_client.post(
        f"/person-resolution/reviews/{decision_id}/decision", json={"action": "link_to_person"}
    )
    bad_action = db_client.post(
        f"/person-resolution/reviews/{decision_id}/decision", json={"action": "auto_merge"}
    )

    assert missing_person.status_code == 409
    assert bad_action.status_code == 422
