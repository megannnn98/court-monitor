"""Each work-cycle review kind has one focused page; legacy bookmarks redirect."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from db.orm_models import (
    EntityGroupPoliticsRecord,
    EntityGroupRecord,
    EntityGroupRoleRecord,
    EntityPoliticsDecisionRecord,
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


def test_review_pages_are_focused_and_link_back_to_the_cycle(
    session_factory: sessionmaker[Session],
) -> None:
    with _client(session_factory) as client:
        pages = {
            path: client.get(path) for path in ("/ui/pairs", "/ui/roles", "/ui/politics-review")
        }

    assert {path: response.status_code for path, response in pages.items()} == dict.fromkeys(
        pages, 200
    )
    assert "Спорные совпадения людей" in pages["/ui/pairs"].text
    assert "Неясная роль в деле" in pages["/ui/roles"].text
    assert "Неясная политичность" in pages["/ui/politics-review"].text
    for page in pages.values():
        assert '<a href="/ui/cycle">Назад к циклу</a>' in page.text


def test_legacy_queue_management_and_disputes_addresses_redirect(
    session_factory: sessionmaker[Session],
) -> None:
    with _client(session_factory) as client:
        queue = client.get("/ui/queue", follow_redirects=False)
        disputes = client.get("/ui/disputes", follow_redirects=False)
        management = client.get("/ui/management?run_id=7", follow_redirects=False)

    assert (queue.status_code, queue.headers["location"]) == (303, "/ui/pairs")
    assert (disputes.status_code, disputes.headers["location"]) == (303, "/ui/pairs")
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
        roles = client.get("/ui/roles").text
        politics = client.get("/ui/politics-review").text

    assert 'action="/ui/roles/decide"' in roles
    assert '<button name="role" value="figurant" type="submit">Фигурант</button>' in roles
    assert (
        '<button name="role" value="mentioned" type="submit" class="secondary">Только упомянут</button>'
        in roles
    )
    assert 'action="/ui/politics-review/decide"' in politics
    assert (
        '<button name="verdict" value="political" type="submit">Политическое</button>' in politics
    )
    assert (
        '<button name="verdict" value="criminal" type="submit" class="secondary">'
        "Обычное уголовное</button>" in politics
    )


def test_deciding_a_role_applies_it_and_keeps_the_decision(
    session_factory: sessionmaker[Session],
) -> None:
    _unclear(session_factory)

    with _client(session_factory) as client:
        response = client.post(
            "/ui/roles/decide",
            data={"key": "фёдор сирош", "role": "figurant"},
            follow_redirects=False,
        )
        page = client.get("/ui/roles").text
        broken = client.post("/ui/roles/decide", data={"key": "фёдор сирош", "role": "unclear"})

    assert response.status_code == 303 and response.headers["location"] == "/ui/roles"
    # The row left the review list.
    assert "Сирош" not in page
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
            "/ui/politics-review/decide",
            data={"key": "александр беда", "verdict": "political"},
            follow_redirects=False,
        )
        page = client.get("/ui/politics-review").text

    assert response.status_code == 303 and response.headers["location"] == "/ui/politics-review"
    assert "Беда" not in page
    with session_factory() as session:
        decision = session.get(EntityPoliticsDecisionRecord, "александр беда")
        verdict = session.scalar(
            select(EntityGroupPoliticsRecord)
            .join(EntityGroupRecord, EntityGroupRecord.id == EntityGroupPoliticsRecord.group_id)
            .where(EntityGroupRecord.key == "александр беда")
        )
    assert decision is not None and decision.verdict == "political"
    assert verdict is not None and (verdict.verdict, verdict.method) == ("political", "manual")
