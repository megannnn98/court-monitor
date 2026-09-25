"""«Спорные случаи»: two entities side by side, and a person's decision."""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from db.orm_models import (
    EntityGroupRecord,
    EntityGroupRfMatchRecord,
    EntityPairDecisionRecord,
    RosfinmonitoringEntryRecord,
    RosfinmonitoringSnapshotRecord,
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


def _regions(key: str) -> list[list[object]]:
    """A card's region as the card writes it: from the key, or Ранав's."""
    if " · " in key:
        return [[key.split(" · ")[1].capitalize(), 1]]
    return [["Чукотский автономный округ", 1]] if key == "игорь александрович ранав" else []


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
                    regions=_regions(key),
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


def test_a_pair_with_a_listed_side_left_to_a_person_says_why(
    session_factory: sessionmaker[Session],
) -> None:
    _entities(
        session_factory,
        ("денис попов", "Денис Попов", 63),
        ("денис владимирович попов · курская область", "Денис Владимирович Попов", 6),
        ("денис александрович попов", "Денис Александрович Попов", 2),
        # Listed too, but the only one of its name: no «several people» to explain (the
        # next check merges it).
        ("игорь ранав", "Игорь Ранав", 13),
        ("игорь александрович ранав", "Игорь Александрович Ранав", 2),
    )
    with session_factory.begin() as session:
        snapshot = RosfinmonitoringSnapshotRecord(
            snapshot_date=datetime(2026, 9, 25, tzinfo=UTC),
            source_url="https://example.test",
            content_hash="x",
            entry_count=1,
            fetched_at=datetime(2026, 9, 25, tzinfo=UTC),
        )
        session.add(snapshot)
        session.flush()
        entry = RosfinmonitoringEntryRecord(
            snapshot_id=snapshot.id,
            full_name="ПОПОВ ДЕНИС ВЛАДИМИРОВИЧ",
            normalized_name="попов денис владимирович",
            matching_key="поповденисвладимирович",
        )
        session.add(entry)
        session.flush()
        for key in ("денис владимирович попов · курская область", "игорь александрович ранав"):
            listed = session.scalar(
                select(EntityGroupRecord.id).where(EntityGroupRecord.key == key)
            )
            session.add(EntityGroupRfMatchRecord(group_id=listed, entry_id=entry.id, level="full"))

    with _client(session_factory) as client:
        page = client.get("/ui/disputes").text

    assert "Нерешённых пар: 3." in page
    # Both pairs of «Денис Попов» say why; only the listed one says it is on the list.
    assert page.count("Не слито автоматически") == 2
    assert page.count("Не слито автоматически, хотя одна сторона в перечне") == 1
    assert "«Попов Денис» подходит сразу к нескольким людям" in page
    # The card's region, as the card wrote it, tells namesakes of one full name apart.
    assert "«Попов Денис Владимирович» (Курская область), «Попов Денис Александрович»" in page
