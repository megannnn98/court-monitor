"""`GET /api/v1/sentences`: «Приговоры» for the React console, read through the legacy
page's own `read_sentences`."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from fastapi.testclient import TestClient
from sqlalchemy import update
from sqlalchemy.orm import Session, sessionmaker
from support.db_fixtures import DatabaseSeeder

from db.orm_models import ArticleSentenceRecord
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


def _sentence(article_id: int, person: str, region: str, months: int) -> ArticleSentenceRecord:
    return ArticleSentenceRecord(
        article_id=article_id,
        person=person,
        person_key=person.lower(),
        region=region,
        kind="colony",
        months=months,
        fine_rub=0,
        in_absentia=False,
        sentenced_on="2026-09-24",
        articles=["207.3"],
        reason="antiwar_speech",
        reason_text="посты о войне",
        quote=f"приговорил {person}",
    )


def _seed(session_factory: sessionmaker[Session]) -> int:
    with session_factory.begin() as session:
        seed = DatabaseSeeder(session)
        source = seed.source("news", "https://news.example.test")
        article, _ = seed.article(
            source, external_id="one", title="Два приговора", text="Суд вынес два приговора."
        )
        session.flush()
        session.add_all(
            [
                _sentence(article, "Петров Иван", "Москва", 84),
                _sentence(article, "Сидоров Олег", "Тульская область", 30),
            ]
        )
    return article


def test_the_cases_are_the_legacy_page_s(session_factory: sessionmaker[Session]) -> None:
    article = _seed(session_factory)

    with _client(session_factory) as client:
        body = client.get("/api/v1/sentences").json()
        page = client.get("/ui/sentences").text

    assert body["total"] == 2 and f"Найдено: {body['total']}." in page
    assert (body["cases"], body["hidden"], body["view"]) == (2, 0, "")
    by_person = {row["person"]: row for row in body["items"]}
    assert set(by_person) == {"Петров Иван", "Сидоров Олег"}
    petrov = by_person["Петров Иван"]
    assert (petrov["term"], petrov["region"], petrov["article_id"]) == ("7 г.", "Москва", article)
    assert by_person["Сидоров Олег"]["term"] == "2 г. 6 мес."
    for row in body["items"]:
        assert row["kind_label"] in page and row["reason_label"] in page
    assert body["regions"] == ["Москва", "Тульская область"]
    assert body["reasons"][:2] == [
        {"value": "", "label": "Все дела", "count": None},
        {"value": "political", "label": "Все политические", "count": None},
    ]


def test_the_filters_narrow_and_an_unknown_reason_is_political(
    session_factory: sessionmaker[Session],
) -> None:
    _seed(session_factory)

    with _client(session_factory) as client:
        moscow = client.get("/api/v1/sentences", params={"region": "Москва"}).json()
        religion = client.get("/api/v1/sentences", params={"reason": "religion"}).json()
        unknown = client.get("/api/v1/sentences", params={"reason": "nonsense"}).json()

    assert [row["person"] for row in moscow["items"]] == ["Петров Иван"]
    assert religion["items"] == [] and religion["total"] == 0
    assert unknown["reason"] == "political" and unknown["total"] == 2


def test_what_was_taken_out_is_a_list_of_its_own(session_factory: sessionmaker[Session]) -> None:
    _seed(session_factory)
    with session_factory.begin() as session:
        session.execute(
            update(ArticleSentenceRecord)
            .where(ArticleSentenceRecord.person == "Петров Иван")
            .values(hidden=True)
        )

    with _client(session_factory) as client:
        listed = client.get("/api/v1/sentences").json()
        hidden = client.get("/api/v1/sentences", params={"view": "hidden"}).json()

    assert [row["person"] for row in listed["items"]] == ["Сидоров Олег"]
    assert listed["hidden"] == 1
    assert [row["person"] for row in hidden["items"]] == ["Петров Иван"]
    assert hidden["items"][0]["source"] is None and hidden["view"] == "hidden"
