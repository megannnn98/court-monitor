"""`GET /api/v1/investigations` and `/investigations/{key}`: «Найти человека» and the
dossier for the React console, read through the legacy page's own `search` and `load`."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker
from support.db_fixtures import DatabaseSeeder

from db.orm_models import EntityGroupPoliticsRecord, EntityGroupRoleRecord, EntityMentionRecord
from entities.collector import EntityCollector
from web.app import app
from web.dependencies import get_db

MOOR = "александр моор"
ARREST = "Суд арестовал Александра Моора по ч. 2 ст. 205.2 УК РФ."
QUOTE = "Александра Моора арестовали за антивоенные посты"


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


def _mention(
    session: Session,
    seed: DatabaseSeeder,
    run: int,
    surface: str,
    kind: str,
    data: dict[str, object],
) -> int:
    mention_id = seed.mention(run, surface, person_id=None, entity_type=kind)
    session.get_one(EntityMentionRecord, mention_id).normalized_data = data
    return mention_id


def _case(session_factory: sessionmaker[Session]) -> int:
    """Моор arrested under 205.2 by a named court, judged political by the model."""
    with session_factory() as session:
        seed = DatabaseSeeder(session)
        source = seed.source("ОВД-Инфо", "https://ovd.info")
        article, run = seed.article(
            source,
            external_id="arrest",
            title="Арест Моора",
            text=f"{ARREST} Ленинский суд. {QUOTE}.",
            published_at=datetime(2026, 9, 1, 9, tzinfo=UTC),
        )
        moor = _mention(
            session,
            seed,
            run,
            "Александра Моора",
            "person",
            {"first_name": "Александр", "last_name": "Моора", "patronymic": None},
        )
        law = _mention(
            session,
            seed,
            run,
            "ч. 2 ст. 205.2 УК РФ",
            "legal_reference",
            {"code": "УК РФ", "article": "205.2", "part": "2", "clause": None},
        )
        court = seed.mention(run, "Ленинский суд", person_id=None, entity_type="court")
        seed.event(
            run,
            ARREST,
            event_type="arrest",
            event_date=datetime(2026, 9, 1, 9, tzinfo=UTC),
            links=[],
            entity_links=[(moor, "target"), (law, "legal_basis"), (court, "court")],
        )
        session.commit()
    EntityCollector(session_factory).run()
    with session_factory.begin() as session:
        group = session.scalar(text("SELECT id FROM entity_groups WHERE key = :key"), {"key": MOOR})
        session.add(
            EntityGroupRoleRecord(
                group_id=group,
                role="figurant",
                kind="accused",
                method="model",
                reason="Арестован по уголовному делу.",
                quote=ARREST,
            )
        )
        session.add(
            EntityGroupPoliticsRecord(
                group_id=group,
                verdict="political",
                method="model",
                reason="Преследование за антивоенные посты.",
                quote=QUOTE,
            )
        )
    return article


def test_the_search_finds_by_any_form_and_opens_on_the_latest_cases(
    session_factory: sessionmaker[Session],
) -> None:
    _case(session_factory)

    with _client(session_factory) as client:
        latest = client.get("/api/v1/investigations").json()
        found = client.get("/api/v1/investigations", params={"q": "Моора"}).json()
        nobody = client.get("/api/v1/investigations", params={"q": "Никто"}).json()
        page = client.get("/ui/investigations").text

    assert latest["heading"] == "Свежие политические дела"
    assert [item["name"] for item in latest["items"]] == ["Моора Александр"]
    assert latest["items"][0]["name"] in page
    assert latest["items"][0]["role_label"] == "фигурант дела"
    assert found["heading"] == "Найдено по «Моора»" and found["items"][0]["key"] == MOOR
    assert nobody["items"] == []


def test_the_dossier_says_what_the_legacy_page_says(session_factory: sessionmaker[Session]) -> None:
    article = _case(session_factory)

    with _client(session_factory) as client:
        dossier = client.get(f"/api/v1/investigations/{MOOR}").json()
        page = client.get(f"/ui/investigations/{MOOR}").text

    assert dossier["name"] == "Моора Александр" and dossier["name"] in page
    assert dossier["role_label"] == "фигурант дела"
    assert dossier["verdict"] == "political"
    assert dossier["verdict_reason"] == "Преследование за антивоенные посты."
    assert dossier["verdict_method_label"] == "модель по цитатам из публикаций"
    assert dossier["verdict_source_article_id"] == article
    assert dossier["rf_label"] == "не найден в перечне" and dossier["rf_label"] in page
    for warning in dossier["warnings"]:
        assert warning in page
    assert [
        (charge["article"], charge["parts"], charge["political"]) for charge in dossier["charges"]
    ] == [("205.2", ["2"], True)]
    (item,) = dossier["timeline"]
    assert (item["event_type"], item["label"], item["articles"]) == ("arrest", "Арест", ["205.2"])
    assert item["orgs"] == [{"name": "Ленинский суд", "role_label": "суд"}]
    (source,) = item["sources"]
    assert source["quote"][source["start"] : source["end"]] == ARREST
    (publication,) = dossier["publications"]
    assert publication["article_id"] == article and publication["identification"] is None
    assert (
        dossier["graph_url"]
        == "/api/investigations/%D0%B0%D0%BB%D0%B5%D0%BA%D1%81%D0%B0%D0%BD%D0%B4%D1%80%20%D0%BC%D0%BE%D0%BE%D1%80/graph"
    )
    assert dossier["known"] == {
        "loaded": False,
        "not_in_base": False,
        "label": None,
        "names": [],
        "more": 0,
    }


def test_an_unknown_person_is_not_found(session_factory: sessionmaker[Session]) -> None:
    with _client(session_factory) as client:
        missing = client.get("/api/v1/investigations/никто никтов")

    assert missing.status_code == 404 and missing.json() == {"detail": "Человек не найден"}
