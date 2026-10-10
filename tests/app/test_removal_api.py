"""`POST /api/v1/entities/removal` and `GET /api/v1/entities/removals`: a person removes
an entity that is nobody, sees what was removed and takes a removal back."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker
from support.db_fixtures import DatabaseSeeder

from db.orm_models import EntityMentionRecord
from entities.collector import EntityCollector
from web.app import app
from web.dependencies import get_db

PUTIN = "дмитрий путин"


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


def _case(session_factory: sessionmaker[Session]) -> None:
    with session_factory() as session:
        seed = DatabaseSeeder(session)
        source = seed.source("news", "https://news.example.test")
        _, run = seed.article(
            source, external_id="a", title="a", text="Суд арестовал Дмитрия Путина."
        )
        mention_id = seed.mention(run, "Дмитрия Путина", person_id=None)
        session.get_one(EntityMentionRecord, mention_id).normalized_data = {
            "first_name": "Дмитрий",
            "last_name": "Путин",
            "patronymic": None,
        }
        seed.event(run, "арестовал", event_type="arrest", event_date=None, links=[])
        session.commit()
    EntityCollector(session_factory).run()


def test_a_removed_person_leaves_the_list_and_the_dossier_and_is_listed_as_removed(
    session_factory: sessionmaker[Session],
) -> None:
    _case(session_factory)
    with _client(session_factory) as client:
        before = client.get("/api/v1/entities", params={"role": "all"}).json()
        removed = client.post("/api/v1/entities/removal", json={"key": PUTIN, "removed": True})
        after = client.get("/api/v1/entities", params={"role": "all"}).json()
        dossier = client.get(f"/api/v1/investigations/{PUTIN}")
        listed = client.get("/api/v1/entities/removals").json()

    assert [row["key"] for row in before["items"]] == [PUTIN]
    assert removed.status_code == 200
    assert removed.json() == {"key": PUTIN, "name": "Путин Дмитрий", "removed": True}
    assert after["items"] == []
    assert dossier.status_code == 404
    assert listed == {"items": [{"key": PUTIN, "name": "Путин Дмитрий", "removed": True}]}


def test_a_removal_is_taken_back_and_the_person_returns_at_the_next_rebuild(
    session_factory: sessionmaker[Session],
) -> None:
    _case(session_factory)
    with _client(session_factory) as client:
        client.post("/api/v1/entities/removal", json={"key": PUTIN, "removed": True})
        back = client.post("/api/v1/entities/removal", json={"key": PUTIN, "removed": False})
        listed = client.get("/api/v1/entities/removals").json()
        still_gone = client.get("/api/v1/entities", params={"role": "all"}).json()
        EntityCollector(session_factory).run()
        returned = client.get("/api/v1/entities", params={"role": "all"}).json()

    assert back.json() == {"key": PUTIN, "name": "Путин Дмитрий", "removed": False}
    assert listed == {"items": []}
    assert still_gone["items"] == []
    assert [row["key"] for row in returned["items"]] == [PUTIN]


def test_nobody_to_remove_and_no_removal_to_take_back_are_not_found(
    session_factory: sessionmaker[Session],
) -> None:
    with _client(session_factory) as client:
        nobody = client.post("/api/v1/entities/removal", json={"key": "никто", "removed": True})
        nothing = client.post("/api/v1/entities/removal", json={"key": "никто", "removed": False})

    assert (nobody.status_code, nobody.json()["detail"]) == (404, "Человек не найден")
    assert (nothing.status_code, nothing.json()["detail"]) == (404, "Удаление не найдено")
