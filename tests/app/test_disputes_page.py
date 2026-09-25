"""«Спорные случаи»: two entities side by side, and a person's decision."""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from db.orm_models import EntityGroupRecord, EntityPairDecisionRecord
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


def _entities(session_factory: sessionmaker[Session], *entities: tuple[str, str, int]) -> None:
    with session_factory.begin() as session:
        for key, name, mentions in entities:
            session.add(
                EntityGroupRecord(
                    key=key,
                    name=name,
                    variants=[[name, mentions]],
                    mention_count=mentions,
                    article_count=mentions,
                    event_types={},
                    regions=[["Чукотский автономный округ", 1]] if "александрович" in key else [],
                )
            )


PEOPLE = (
    ("игорь ранав", "Игорь Ранав", 13),
    ("игорь александрович ранав", "Игорь Александрович Ранав", 2),
    ("лида мониава", "Лида Мониава", 99),
    ("лидия мониава", "Лидия Мониава", 5),
)


def test_the_pairs_are_listed_side_by_side_and_filtered_by_kind(
    session_factory: sessionmaker[Session],
) -> None:
    _entities(session_factory, *PEOPLE)

    with _client(session_factory) as client:
        page = client.get("/ui/disputes").text
        similar = client.get("/ui/disputes", params={"kind": "similar"}).text

    assert 'href="/ui/disputes">Спорные случаи</a>' in page
    assert "Нерешённых пар: 2." in page
    assert "С отчеством и без (1)" in page and "Похожее имя (1)" in page
    assert re.search(r"Ранав Игорь</a>.*?Ранав Игорь Александрович</a>", page, re.DOTALL)
    assert "Регион: Чукотский автономный округ" in page
    assert '<button name="decision" value="same" type="submit">Один человек</button>' in page
    assert "Нерешённых пар: 1." in similar and "Мониава Лида" in similar
    assert "Ранав" not in similar


def test_one_person_merges_and_different_people_leave_the_list(
    session_factory: sessionmaker[Session],
) -> None:
    _entities(session_factory, *PEOPLE)

    with _client(session_factory) as client:
        same = client.post(
            "/ui/disputes/decide",
            data={
                "key_a": "игорь ранав",
                "key_b": "игорь александрович ранав",
                "decision": "same",
                "kind": "all",
                "page": "1",
            },
            follow_redirects=False,
        )
        client.post(
            "/ui/disputes/decide",
            data={"key_a": "лида мониава", "key_b": "лидия мониава", "decision": "different"},
        )
        page = client.get("/ui/disputes").text
        broken = client.post("/ui/disputes/decide", data={"key_a": "лида мониава"})

    assert same.status_code == 303 and same.headers["location"] == "/ui/disputes?kind=all&page=1"
    assert "Спорных пар нет." in page
    assert broken.status_code == 400
    with session_factory() as session:
        names = {
            entity.name: entity.mention_count
            for entity in session.scalars(select(EntityGroupRecord))
        }
        decisions = {
            (record.key_a, record.key_b): record.decision
            for record in session.scalars(select(EntityPairDecisionRecord))
        }
    # Merged into the fuller name (the counts are recounted from mention links, which
    # these bare rows lack: `test_disputes` checks them).
    assert set(names) == {"Игорь Александрович Ранав", "Лида Мониава", "Лидия Мониава"}
    assert decisions == {
        ("игорь александрович ранав", "игорь ранав"): "same",
        ("лида мониава", "лидия мониава"): "different",
    }
