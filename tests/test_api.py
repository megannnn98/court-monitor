"""Tests for FastAPI application."""

from collections.abc import Callable, Iterator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from research_db_fixtures import ResearchSeeder
from research_report_fixtures import SNAPSHOT_ID, person_result, rosfin
from research_report_fixtures import request as report_request
from research_report_fixtures import response as report_response
from research_workflow_fakes import FakeRequestParser, FakeResearchService, FakeSnapshotLookup
from sqlalchemy.orm import Session, sessionmaker

from api import (
    _get_research_graph,
    _get_session_factory,
    app,
    get_db,
    get_research_query_graph,
    get_research_service,
)
from candidate_query_models import RosfinmonitoringStatus
from research_models import ResearchRequest, ResearchResponse
from research_planning.planner import ResearchPlanner
from research_service import ResearchSnapshotNotFoundError
from research_workflow.graph import ResearchGraph, build_research_graph
from research_workflow.llm import (
    LlmAuthenticationError,
    LlmError,
    LlmInvalidResponseError,
    LlmRateLimitError,
    LlmTimeoutError,
    LlmUnavailableError,
)
from research_workflow.models import ResearchIntake, UnsupportedCriterion
from source_registry import SOURCES


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


def _query_graph(
    parser: FakeRequestParser, service: FakeResearchService | None = None
) -> ResearchGraph:
    return build_research_graph(
        request_parser=parser,
        research_service=service or FakeResearchService(),
        snapshot_lookup=FakeSnapshotLookup(),
        planner=ResearchPlanner(SOURCES),
    )


@pytest.fixture
def override_query_graph() -> Iterator[Callable[[ResearchGraph], TestClient]]:
    def install(graph: ResearchGraph) -> TestClient:
        app.dependency_overrides[get_research_query_graph] = lambda: graph
        return TestClient(app)

    try:
        yield install
    finally:
        app.dependency_overrides.pop(get_research_query_graph, None)


def test_research_query_runs_workflow_and_returns_structured_result(
    override_query_graph: Callable[[ResearchGraph], TestClient],
) -> None:
    service = FakeResearchService()
    client = override_query_graph(
        _query_graph(
            FakeRequestParser(
                intake=ResearchIntake(
                    request={
                        "object_type": "person",
                        "criteria": {"persecution_status": "political"},
                    }
                )
            ),
            service,
        )
    )

    response = client.post("/research/query", json={"query": "  Найди политически преследуемых  "})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["request"]["criteria"]["persecution_status"] == "political"
    assert (body["results"], body["warnings"]) == ([], [])
    assert (body["clarification_required"], body["review_required"]) == (False, False)
    assert (body["total_matched"], body["error"]) == (0, None)
    assert len(service.requests) == 1
    # Report-level fields next to the unchanged raw fields.
    assert body["plan"]["database_search"] is True
    assert body["report"]["status"] == "insufficient_data"
    assert body["report"]["source_refresh_recommended"] is True
    assert set(body["report"]["recommended_sources"]) == {"ovd-info", "sota-vision"}


def test_research_query_clarification_is_200_with_question(
    override_query_graph: Callable[[ResearchGraph], TestClient],
) -> None:
    client = override_query_graph(
        _query_graph(
            FakeRequestParser(
                intake=ResearchIntake(
                    request={"object_type": "person"},
                    unsupported_criteria=[UnsupportedCriterion(criterion="age", value="30 лет")],
                )
            )
        )
    )

    response = client.post("/research/query", json={"query": "люди 30 лет"})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "clarification_required"
    assert body["clarification_required"] is True
    assert body["unsupported_criteria"] == [{"criterion": "age", "value": "30 лет"}]


@pytest.mark.parametrize(
    ("error", "status_code", "code"),
    [
        (LlmTimeoutError("t"), 504, "llm_timeout"),
        (LlmUnavailableError("u"), 503, "llm_unavailable"),
        (LlmRateLimitError("r"), 503, "llm_rate_limited"),
        (LlmAuthenticationError("a"), 502, "llm_authentication_failed"),
        (LlmInvalidResponseError("i"), 502, "llm_invalid_output"),
    ],
)
def test_research_query_provider_failure_is_not_an_empty_success(
    override_query_graph: Callable[[ResearchGraph], TestClient],
    error: LlmError,
    status_code: int,
    code: str,
) -> None:
    client = override_query_graph(_query_graph(FakeRequestParser(error=error)))

    response = client.post("/research/query", json={"query": "что угодно"})

    assert response.status_code == status_code
    body = response.json()
    assert (body["status"], body["error"]["code"], body["total_matched"]) == ("failed", code, None)


@pytest.mark.parametrize("body", [{}, {"query": ""}, {"query": "   "}, {"query": "x", "limit": 5}])
def test_research_query_rejects_invalid_body(
    override_query_graph: Callable[[ResearchGraph], TestClient], body: dict[str, object]
) -> None:
    parser = FakeRequestParser(error=LlmTimeoutError("must not be called"))
    client = override_query_graph(_query_graph(parser))

    assert client.post("/research/query", json=body).status_code == 422
    assert parser.queries == []


def test_research_query_without_together_config_returns_503(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://user:pass@localhost:1/none")
    monkeypatch.delenv("TOGETHER_API_KEY", raising=False)
    monkeypatch.delenv("TOGETHER_MODEL", raising=False)
    _get_session_factory.cache_clear()
    _get_research_graph.cache_clear()
    try:
        response = TestClient(app).post("/research/query", json={"query": "x"})
    finally:
        _get_session_factory.cache_clear()
        _get_research_graph.cache_clear()

    assert response.status_code == 503
    assert "TOGETHER_API_KEY" in response.json()["detail"]


def test_research_query_unexpected_error_is_structured_500(
    override_query_graph: Callable[[ResearchGraph], TestClient],
) -> None:
    client = override_query_graph(
        _query_graph(
            FakeRequestParser(intake=ResearchIntake(request={"object_type": "person"})),
            FakeResearchService(error=RuntimeError("[parameters: {'p': '%Иванов%'}]")),
        )
    )

    response = client.post("/research/query", json={"query": "все"})

    assert response.status_code == 500
    body = response.json()
    assert (body["status"], body["error"]["code"]) == ("failed", "workflow_unexpected_error")
    assert "Иванов" not in response.text


# --- POST /research/reviews ----------------------------------------------------------


def test_research_review_endpoint_is_idempotent(
    db_client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    with session_factory() as session:
        seed = ResearchSeeder(session)
        person_id = seed.person("Иван Иванов")
        snapshot_id = seed.snapshot()
        match_id = seed.match(person_id, snapshot_id, "insufficient_data", 0.3)
        session.commit()
    body = {
        "person_id": person_id,
        "reason": "rosfin_insufficient_data",
        "snapshot_id": snapshot_id,
    }

    created = db_client.post("/research/reviews", json=body)
    repeated = db_client.post("/research/reviews", json=body)

    assert created.status_code == 201
    assert repeated.status_code == 200
    assert created.json()["review_id"] == repeated.json()["review_id"]
    assert (created.json()["created"], repeated.json()["created"]) == (True, False)
    assert created.json()["subject_type"] == "rosfinmatch"
    assert created.json()["subject_id"] == match_id
    reviews = db_client.get("/reviews", params={"status": "pending"}).json()
    assert [review["id"] for review in reviews] == [created.json()["review_id"]]


def test_research_review_endpoint_rejects_stale_condition_and_unknown_subject(
    db_client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    with session_factory() as session:
        seed = ResearchSeeder(session)
        person_id = seed.person("Иван Иванов")
        snapshot_id = seed.snapshot()
        seed.match(person_id, snapshot_id, "not_matched", 0.8)
        session.commit()

    stale = db_client.post(
        "/research/reviews",
        json={"person_id": person_id, "reason": "rosfin_ambiguous", "snapshot_id": snapshot_id},
    )
    unknown = db_client.post(
        "/research/reviews",
        json={
            "person_id": person_id + 100,
            "reason": "rosfin_ambiguous",
            "snapshot_id": snapshot_id,
        },
    )

    assert stale.status_code == 409
    assert unknown.status_code == 404
    assert db_client.get("/reviews").json() == []


@pytest.mark.parametrize(
    "body",
    [
        {"person_id": 1, "reason": "missing_evidence", "classification_id": 1},
        {"person_id": 1, "reason": "rosfin_ambiguous"},
        {"person_id": 1, "reason": "rosfin_ambiguous", "snapshot_id": 1, "note": "free text"},
        {"person_id": 1, "reason": "made_up", "snapshot_id": 1},
    ],
)
def test_research_review_endpoint_rejects_invalid_body(body: dict[str, object]) -> None:
    def unbound_session() -> Iterator[Session]:
        # Never connects: validation fails before the endpoint body runs.
        yield Session()

    app.dependency_overrides[get_db] = unbound_session
    try:
        assert TestClient(app).post("/research/reviews", json=body).status_code == 422
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_research_review_endpoint_returns_404_for_inactive_person(
    db_client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    with session_factory() as session:
        seed = ResearchSeeder(session)
        person_id = seed.person("Иван Иванов", status="merged")
        snapshot_id = seed.snapshot()
        seed.match(person_id, snapshot_id, "ambiguous", 0.5)
        session.commit()

    response = db_client.post(
        "/research/reviews",
        json={"person_id": person_id, "reason": "rosfin_ambiguous", "snapshot_id": snapshot_id},
    )

    assert response.status_code == 404
    assert db_client.get("/reviews").json() == []


def test_research_query_review_condition_is_a_200_report_not_an_error(
    override_query_graph: Callable[[ResearchGraph], TestClient],
) -> None:
    research_request = report_request(snapshot_id=SNAPSHOT_ID)
    ambiguous = person_result(rf=rosfin(RosfinmonitoringStatus.AMBIGUOUS))
    client = override_query_graph(
        _query_graph(
            FakeRequestParser(intake=ResearchIntake(request={"object_type": "person"})),
            FakeResearchService(response=report_response(research_request, [ambiguous])),
        )
    )

    response = client.post("/research/query", json={"query": "все"})

    assert response.status_code == 200
    body = response.json()
    assert (body["status"], body["error"], body["review_required"]) == ("completed", None, True)
    report = body["report"]
    assert report["status"] == "review_required"
    assert report["summary"]["review_required_count"] == 1
    (item,) = report["items"]
    assert item["rosfinmonitoring_status"] == "ambiguous"
    assert [reason["code"] for reason in item["review"]["reasons"]] == ["rosfin_ambiguous"]
    assert item["review"]["reasons"][0]["snapshot_id"] == SNAPSHOT_ID
    assert all(claim["supported"] for claim in item["claims"])
    # Raw result stays next to the report.
    assert body["results"][0]["rosfinmonitoring"]["status"] == "ambiguous"
