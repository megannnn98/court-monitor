"""The review stations of the work cycle, read and decided through `/api/v1/review`."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from analysis.storage.models import EntityGroupPoliticsRecord, EntityPoliticsDecisionRecord
from processing.storage.models import (
    EntityGroupRecord,
    EntityGroupRoleRecord,
    EntityRoleDecisionRecord,
)
from web.app import app
from web.dependencies import get_db


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


def test_the_old_journal_address_redirects(session_factory: sessionmaker[Session]) -> None:
    with _client(session_factory) as client:
        management = client.get("/ui/management?run_id=7", follow_redirects=False)

    assert (management.status_code, management.headers["location"]) == (
        303,
        "/ui/runs?run_id=7",
    )


def _unclear(session_factory: sessionmaker[Session]) -> None:
    """One entity step 4 left unclear, one step 5 did."""
    with session_factory.begin() as session:
        sirosh = EntityGroupRecord(
            key="фёдор сирош",
            name="Фёдор Сирош",
            variants=[],
            mention_count=3,
            article_count=3,
            event_types={},
            regions=[],
        )
        beda = EntityGroupRecord(
            key="александр беда",
            name="Александр Беда",
            variants=[],
            mention_count=5,
            article_count=5,
            event_types={},
            regions=[],
        )
        session.add_all([sirosh, beda])
        session.flush()
        session.add(
            EntityGroupRoleRecord(
                group_id=sirosh.id,
                role="unclear",
                kind=None,
                method="model",
                reason="модель не ответила",
                quote="",
            )
        )
        session.add(
            EntityGroupPoliticsRecord(
                group_id=beda.id,
                verdict="unclear",
                method="model",
                reason="модель не ответила",
                quote="",
            )
        )


def test_the_role_and_politics_pages_carry_the_decision_buttons(
    session_factory: sessionmaker[Session],
) -> None:
    _unclear(session_factory)

    with _client(session_factory) as client:
        roles = client.get("/api/v1/review/roles").json()
        politics = client.get("/api/v1/review/politics").json()

    assert roles["title"] == "Неясная роль в деле"
    assert [item["name"] for item in roles["items"]] == ["Сирош Фёдор"]
    role_choices = {option["value"]: option["label"] for option in roles["choices"]}
    assert role_choices["figurant"] == "Фигурант" and role_choices["mentioned"] == "Только упомянут"
    assert politics["title"] == "Неясная политичность"
    assert [item["name"] for item in politics["items"]] == ["Беда Александр"]
    verdicts = {option["value"]: option["label"] for option in politics["choices"]}
    assert verdicts == {"political": "Политическое", "criminal": "Обычное уголовное"}


def test_deciding_a_role_applies_it_and_keeps_the_decision(
    session_factory: sessionmaker[Session],
) -> None:
    _unclear(session_factory)

    with _client(session_factory) as client:
        response = client.post(
            "/api/v1/review/roles/decide", json={"key": "фёдор сирош", "role": "figurant"}
        )
        listed = client.get("/api/v1/review/roles").json()
        broken = client.post(
            "/api/v1/review/roles/decide", json={"key": "фёдор сирош", "role": "unclear"}
        )

    assert response.status_code == 200
    # The row left the review list.
    assert listed["items"] == []
    assert broken.status_code == 400
    with session_factory() as session:
        decision = session.get(EntityRoleDecisionRecord, "фёдор сирош")
        role = session.scalar(
            select(EntityGroupRoleRecord)
            .join(EntityGroupRecord, EntityGroupRecord.id == EntityGroupRoleRecord.group_id)
            .where(EntityGroupRecord.key == "фёдор сирош")
        )
    assert decision is not None and decision.role == "figurant"
    assert role is not None and (role.role, role.method) == ("figurant", "manual")


def test_deciding_politics_applies_it_and_keeps_the_decision(
    session_factory: sessionmaker[Session],
) -> None:
    _unclear(session_factory)

    with _client(session_factory) as client:
        response = client.post(
            "/api/v1/review/politics/decide", json={"key": "александр беда", "verdict": "political"}
        )
        listed = client.get("/api/v1/review/politics").json()

    assert response.status_code == 200
    assert listed["items"] == []
    with session_factory() as session:
        decision = session.get(EntityPoliticsDecisionRecord, "александр беда")
        verdict = session.scalar(
            select(EntityGroupPoliticsRecord)
            .join(EntityGroupRecord, EntityGroupRecord.id == EntityGroupPoliticsRecord.group_id)
            .where(EntityGroupRecord.key == "александр беда")
        )
    assert decision is not None and decision.verdict == "political"
    assert verdict is not None and (verdict.verdict, verdict.method) == ("political", "manual")
