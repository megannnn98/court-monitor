"""`/api/v1/review/*`: the review stations for the React console — unclear roles,
unclear politics and the pairs — with the operator's decisions, through the legacy
pages' own functions."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from fastapi.testclient import TestClient
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
    def override() -> Iterator[Session]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)


def _entity(
    key: str, name: str, mentions: int, regions: list[list[object]] | None = None
) -> EntityGroupRecord:
    return EntityGroupRecord(
        key=key,
        name=name,
        variants=[[name, mentions]],
        mention_count=mentions,
        article_count=mentions,
        event_types={},
        regions=regions or [],
    )


def _unclear(session_factory: sessionmaker[Session]) -> None:
    """Сирош: step 4 could not place him; Беда: step 5 could not judge."""
    with session_factory.begin() as session:
        sirosh, beda = (
            _entity("фёдор сирош", "Фёдор Сирош", 3),
            _entity("александр беда", "Александр Беда", 5),
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
                group_id=beda.id, verdict="unclear", method="model", reason="мало данных", quote=""
            )
        )


def test_an_unclear_role_is_listed_and_decided(session_factory: sessionmaker[Session]) -> None:
    _unclear(session_factory)

    with _client(session_factory) as client:
        listed = client.get("/api/v1/review/roles").json()
        decided = client.post(
            "/api/v1/review/roles/decide", json={"key": "фёдор сирош", "role": "figurant"}
        )
        after = client.get("/api/v1/review/roles").json()
        wrong = client.post(
            "/api/v1/review/roles/decide", json={"key": "фёдор сирош", "role": "unclear"}
        )
        nobody = client.post(
            "/api/v1/review/roles/decide", json={"key": "никто", "role": "figurant"}
        )

    assert listed["title"] == "Неясная роль в деле"
    assert [option["value"] for option in listed["choices"]] == [
        "figurant",
        "possible",
        "mentioned",
    ]
    assert listed["items"] == [
        {"key": "фёдор сирош", "name": "Сирош Фёдор", "reason": "модель не ответила"}
    ]
    assert decided.status_code == 200 and decided.json() == {
        "key": "фёдор сирош",
        "decision": "figurant",
    }
    assert after["items"] == []
    assert wrong.status_code == 400 and wrong.json() == {"detail": "Неизвестная роль"}
    assert nobody.status_code == 404
    with session_factory() as session:
        decision = session.get(EntityRoleDecisionRecord, "фёдор сирош")
    assert decision is not None and decision.role == "figurant"


def test_unclear_politics_is_listed_and_decided(session_factory: sessionmaker[Session]) -> None:
    _unclear(session_factory)

    with _client(session_factory) as client:
        listed = client.get("/api/v1/review/politics").json()
        decided = client.post(
            "/api/v1/review/politics/decide", json={"key": "александр беда", "verdict": "political"}
        )
        after = client.get("/api/v1/review/politics").json()
        foreign = client.post(
            "/api/v1/review/politics/decide",
            json={"key": "александр беда", "verdict": "criminal"},
            headers={"Origin": "https://evil.example"},
        )

    assert listed["title"] == "Неясная политичность"
    assert [item["key"] for item in listed["items"]] == ["александр беда"]
    assert decided.json() == {"key": "александр беда", "decision": "political"}
    assert after["items"] == []
    # Another site cannot overturn the decision.
    assert foreign.status_code == 403
    with session_factory() as session:
        decision = session.get(EntityPoliticsDecisionRecord, "александр беда")
    assert decision is not None and decision.verdict == "political"


PEOPLE = (
    ("игорь ранав", "Игорь Ранав", 13),
    ("игорь александрович ранав", "Игорь Александрович Ранав", 2),
    ("лида мониава", "Лида Мониава", 99),
    ("лидия мониава", "Лидия Мониава", 5),
)


def test_the_pairs_are_listed_and_decided(session_factory: sessionmaker[Session]) -> None:
    with session_factory.begin() as session:
        session.add_all([_entity(key, name, mentions) for key, name, mentions in PEOPLE])

    with _client(session_factory) as client:
        listed = client.get("/api/v1/review/pairs").json()
        similar = client.get("/api/v1/review/pairs", params={"kind": "similar"}).json()
        page = client.get("/ui/pairs").text
        same = client.post(
            "/api/v1/review/pairs/decide",
            json={"key_a": "игорь ранав", "key_b": "игорь александрович ранав", "decision": "same"},
        )
        different = client.post(
            "/api/v1/review/pairs/decide",
            json={"key_a": "лида мониава", "key_b": "лидия мониава", "decision": "different"},
        )
        half = client.post(
            "/api/v1/review/pairs/decide",
            json={"key_a": "лида мониава", "key_b": "", "decision": "same"},
        )
        after = client.get("/api/v1/review/pairs").json()

    assert listed["open_pairs"] == 2 and f"Нерешённых пар: {listed['total']}." in page
    assert {option["value"]: option["count"] for option in listed["kinds"]} == {
        "all": 2,
        "patronymic": 1,
        "similar": 1,
        "region": 0,
    }
    names = {(item["left"]["name"], item["right"]["name"]) for item in listed["items"]}
    assert ("Ранав Игорь", "Ранав Игорь Александрович") in names
    assert [item["kind"] for item in similar["items"]] == ["similar"]
    assert similar["items"][0]["hint"].startswith("одна фамилия")
    assert same.status_code == 200 and different.status_code == 200
    assert half.status_code == 400 and half.json() == {"detail": "Неполное решение"}
    assert after["open_pairs"] == 0 and after["decided"]
