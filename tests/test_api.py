"""Tests for FastAPI application."""

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from research_db_fixtures import ResearchSeeder
from sqlalchemy.orm import Session, sessionmaker

from api import _get_session_factory, app, get_db, get_research_service
from research_models import ResearchRequest, ResearchResponse
from research_service import ResearchSnapshotNotFoundError


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


def test_api_has_candidates_endpoint() -> None:
    """Test that candidates endpoint exists."""
    client = TestClient(app)
    response = client.get("/candidates?snapshot_id=1")
    # Should get 503 due to no DATABASE_URL, not 404
    assert response.status_code in (200, 422, 503)


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


class _FakeResearchService:
    def __init__(self, *, snapshot_missing: bool = False) -> None:
        self.requests: list[ResearchRequest] = []
        self.snapshot_missing = snapshot_missing

    def execute(self, request: ResearchRequest) -> ResearchResponse:
        self.requests.append(request)
        if self.snapshot_missing and request.criteria.snapshot_id is not None:
            raise ResearchSnapshotNotFoundError(request.criteria.snapshot_id)
        return ResearchResponse(object_type=request.object_type, request=request, total_matched=0)


@pytest.fixture
def research_client() -> Iterator[tuple[TestClient, _FakeResearchService]]:
    fake = _FakeResearchService(snapshot_missing=True)
    app.dependency_overrides[get_research_service] = lambda: fake
    try:
        yield TestClient(app), fake
    finally:
        app.dependency_overrides.pop(get_research_service, None)


def test_research_endpoint_delegates_validated_request_to_service(
    research_client: tuple[TestClient, _FakeResearchService],
) -> None:
    client, fake = research_client

    response = client.post(
        "/research",
        json={
            "object_type": "person",
            "criteria": {"persecution_status": "political", "event_types": ["arrest"]},
            "limit": 5,
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "object_type": "person",
        "request": {
            "object_type": "person",
            "criteria": {
                "person_id": None,
                "name": None,
                "persecution_status": "political",
                "persecution_min_confidence": None,
                "rosfinmonitoring_status": None,
                "snapshot_id": None,
                "event_types": ["arrest"],
                "date_from": None,
                "date_to": None,
                "source": None,
            },
            "limit": 5,
        },
        "results": [],
        "total_matched": 0,
    }
    assert [r.criteria.persecution_status for r in fake.requests] == ["political"]


@pytest.mark.parametrize(
    "body",
    [
        {"object_type": "person", "limit": 0},
        {"object_type": "article"},
        {"object_type": "person", "criteria": {"rosfinmonitoring_status": "not_matched"}},
        {"object_type": "person", "criteria": {"date_from": "2024-02-01", "date_to": "2024-01-01"}},
        {"object_type": "person", "criteria": {"region": "Москва"}},
    ],
)
def test_research_endpoint_rejects_invalid_requests_before_service(
    research_client: tuple[TestClient, _FakeResearchService], body: dict[str, object]
) -> None:
    client, fake = research_client

    response = client.post("/research", json=body)

    assert response.status_code == 422
    assert fake.requests == []


def test_research_endpoint_maps_unknown_snapshot_to_404(
    research_client: tuple[TestClient, _FakeResearchService],
) -> None:
    client, _ = research_client

    response = client.post(
        "/research", json={"object_type": "person", "criteria": {"snapshot_id": 99}}
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Rosfinmonitoring snapshot 99 not found"


def test_research_endpoint_missing_database_url_returns_503(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    _get_session_factory.cache_clear()

    response = TestClient(app).post("/research", json={"object_type": "person"})

    assert response.status_code == 503


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
