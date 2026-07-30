"""Unit tests: operator web UI.

Every test runs against its own in-memory database wired in through the
``get_session`` dependency, so nothing here touches the working court_monitor.db.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from court_monitor.storage.orm import (
    AuditLog,
    Base,
    ExtractedFact,
    Job,
    MatchCandidate,
    PersonRecord,
    ReviewItem,
    SourceDocument,
)
from court_monitor.web import deps
from court_monitor.web.app import app


@pytest.fixture()
def session() -> Session:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session, future=True)
    s = factory()
    try:
        yield s
    finally:
        s.close()
        engine.dispose()


@pytest.fixture()
def client(session: Session) -> TestClient:
    app.dependency_overrides[deps.get_session] = lambda: session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _seed(session: Session) -> MatchCandidate:
    doc = SourceDocument(
        url="https://example.invalid/a",
        source_type="telegram",
        source_id="testchan",
        title="Приговор по делу",
        content_hash="h1",
        parser_status="parsed",
        relevant=1,
        text="Суд вынес приговор. Иванов Иван Иванович признан виновным.",
    )
    session.add(doc)
    session.flush()

    fact = ExtractedFact(
        document_id=doc.id,
        entity="person",
        field="full_name_original",
        value="Иванов Иван Иванович",
        confidence=0.95,
        quote="приговор. Иванов Иван Иванович признан",
        extraction_method="regex:name:full_fio",
    )
    record = PersonRecord(
        source="rfm",
        raw_name="ИВАНОВ ИВАН ИВАНОВИЧ",
        search_name="иванов иван иванович",
        normalized_name="иванов иван иванович",
        birth_date="1980-01-01",
        birth_place="Г. МОСКВА",
    )
    session.add_all([fact, record])
    session.flush()

    candidate = MatchCandidate(
        extracted_fact_id=fact.id,
        person_record_id=record.id,
        score=0.7,
        status="pending",
        name_score=0.7,
        algorithm_version="match-v3",
        reasons_json='[{"rule": "full_name_morphological_match", "impact": 0.7}]',
        conflicts_json="[]",
    )
    session.add(candidate)
    session.add(
        ReviewItem(
            item_type="source_blocked",
            priority="high",
            source_id="testchan",
            status="pending",
        )
    )
    session.commit()
    return candidate


# ---------------------------------------------------------------------------
# Pages render
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    ["/", "/documents", "/matches?status=pending", "/review?status=pending"],
)
def test_pages_render_on_empty_database(client: TestClient, path: str):
    assert client.get(path).status_code == 200


def test_dashboard_shows_counts(client: TestClient, session: Session):
    _seed(session)
    body = client.get("/").text
    assert "Иванов" not in body  # dashboard lists documents, not facts
    assert "Приговор по делу" in body


def test_dashboard_warns_when_registry_is_empty(client: TestClient):
    """Without registry records matching cannot produce anything — say so."""
    assert "Реестр не загружен" in client.get("/").text


def test_document_detail_groups_facts_and_shows_quote(client: TestClient, session: Session):
    _seed(session)
    body = client.get("/documents/1").text
    assert "full_name_original" in body
    assert "Иванов Иван Иванович" in body
    assert "признан" in body  # the quote


def test_missing_document_is_404(client: TestClient):
    assert client.get("/documents/12345").status_code == 404


def test_missing_candidate_is_404(client: TestClient):
    assert client.get("/matches/12345").status_code == 404


def test_match_detail_shows_both_sides_and_score_breakdown(client: TestClient, session: Session):
    candidate = _seed(session)
    body = client.get(f"/matches/{candidate.id}").text
    assert "Иванов Иван Иванович" in body  # document side
    assert "ИВАНОВ ИВАН ИВАНОВИЧ" in body  # registry side
    assert "1980-01-01" in body
    assert "full_name_morphological_match" in body


# ---------------------------------------------------------------------------
# Decisions
# ---------------------------------------------------------------------------


def test_confirm_updates_status_and_writes_audit(client: TestClient, session: Session):
    candidate = _seed(session)
    resp = client.post(
        f"/matches/{candidate.id}/decide",
        data={
            "decision": "confirmed",
            "comment": "совпали дата и место",
            deps.CSRF_FIELD: deps.csrf_token(),
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303

    session.expire_all()
    stored = session.get(MatchCandidate, candidate.id)
    assert stored.status == "confirmed"
    assert stored.review_comment == "совпали дата и место"
    assert stored.reviewed_at is not None

    entries = session.query(AuditLog).all()
    assert len(entries) == 1
    assert entries[0].action == "match_status_change"
    assert entries[0].actor


def test_reject_updates_status(client: TestClient, session: Session):
    candidate = _seed(session)
    client.post(
        f"/matches/{candidate.id}/decide",
        data={"decision": "rejected", deps.CSRF_FIELD: deps.csrf_token()},
        follow_redirects=False,
    )
    session.expire_all()
    assert session.get(MatchCandidate, candidate.id).status == "rejected"


def test_decision_without_csrf_is_rejected(client: TestClient, session: Session):
    """A decision marks a named person as registry-listed — it must not be
    reachable by a form posted from another page."""
    candidate = _seed(session)
    resp = client.post(
        f"/matches/{candidate.id}/decide",
        data={"decision": "confirmed"},
        follow_redirects=False,
    )
    assert resp.status_code == 403

    session.expire_all()
    assert session.get(MatchCandidate, candidate.id).status == "pending"


def test_decision_with_wrong_csrf_is_rejected(client: TestClient, session: Session):
    candidate = _seed(session)
    resp = client.post(
        f"/matches/{candidate.id}/decide",
        data={"decision": "confirmed", deps.CSRF_FIELD: "not-the-token"},
        follow_redirects=False,
    )
    assert resp.status_code == 403
    session.expire_all()
    assert session.get(MatchCandidate, candidate.id).status == "pending"


def test_unknown_decision_value_is_rejected(client: TestClient, session: Session):
    candidate = _seed(session)
    resp = client.post(
        f"/matches/{candidate.id}/decide",
        data={"decision": "approved", deps.CSRF_FIELD: deps.csrf_token()},
        follow_redirects=False,
    )
    assert resp.status_code == 400
    session.expire_all()
    assert session.get(MatchCandidate, candidate.id).status == "pending"


def test_deciding_a_missing_candidate_is_404(client: TestClient):
    resp = client.post(
        "/matches/999/decide",
        data={"decision": "confirmed", deps.CSRF_FIELD: deps.csrf_token()},
        follow_redirects=False,
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Review items
# ---------------------------------------------------------------------------


def test_review_item_can_be_resolved(client: TestClient, session: Session):
    _seed(session)
    item = session.query(ReviewItem).one()
    resp = client.post(
        f"/review/{item.id}/resolve",
        data={"decision": "resolved", deps.CSRF_FIELD: deps.csrf_token()},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    session.expire_all()
    assert session.get(ReviewItem, item.id).status == "resolved"


def test_review_resolve_requires_csrf(client: TestClient, session: Session):
    _seed(session)
    item = session.query(ReviewItem).one()
    resp = client.post(
        f"/review/{item.id}/resolve", data={"decision": "resolved"}, follow_redirects=False
    )
    assert resp.status_code == 403
    session.expire_all()
    assert session.get(ReviewItem, item.id).status == "pending"


# ---------------------------------------------------------------------------
# Jobs pages
# ---------------------------------------------------------------------------


def test_jobs_page_renders_and_lists_kinds(client: TestClient):
    body = client.get("/jobs").text
    assert "Собрать всё и сопоставить" in body
    assert "Загрузить перечень РФМ" in body


def test_jobs_page_does_not_autorefresh_when_idle(client: TestClient):
    assert 'http-equiv="refresh"' not in client.get("/jobs").text


def test_jobs_page_autorefreshes_while_a_job_is_active(client: TestClient, session: Session):
    session.add(Job(kind="run_all", status="running", actor="tester"))
    session.commit()
    assert 'http-equiv="refresh"' in client.get("/jobs").text


def test_job_detail_shows_error(client: TestClient, session: Session):
    job = Job(kind="run_all", status="failed", actor="tester", error="ValueError: сломалось")
    session.add(job)
    session.commit()
    body = client.get(f"/jobs/{job.id}").text
    assert "ValueError: сломалось" in body


def test_missing_job_is_404(client: TestClient):
    assert client.get("/jobs/4242").status_code == 404


def test_starting_a_job_requires_csrf(client: TestClient):
    resp = client.post("/jobs/start", data={"kind": "generate_matches"}, follow_redirects=False)
    assert resp.status_code == 403


def test_starting_an_unknown_kind_is_409(client: TestClient):
    resp = client.post(
        "/jobs/start",
        data={"kind": "nope", deps.CSRF_FIELD: deps.csrf_token()},
        follow_redirects=False,
    )
    assert resp.status_code == 409
