"""«Список»: the politically persecuted off the Rosfinmonitoring list, and its Excel."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from io import BytesIO

from fastapi.testclient import TestClient
from openpyxl import load_workbook
from sqlalchemy.orm import Session, sessionmaker

from db.orm_models import (
    EntityGroupPoliticsRecord,
    EntityGroupRecord,
    EntityGroupRfMatchRecord,
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


def _seed(session_factory: sessionmaker[Session]) -> None:
    now = datetime.now(UTC)
    with session_factory.begin() as session:
        snapshot = RosfinmonitoringSnapshotRecord(
            snapshot_date=now,
            source_url="https://example.test",
            content_hash="x",
            entry_count=1,
            fetched_at=now,
        )
        session.add(snapshot)
        session.flush()
        entry = RosfinmonitoringEntryRecord(
            snapshot_id=snapshot.id,
            full_name="ИВАНОВ ИВАН ИВАНОВИЧ",
            normalized_name="иванов иван иванович",
            matching_key="ивановиваниванович",
        )
        session.add(entry)
        for key, name, verdict, days, regions in (
            ("анна смирнова", "Анна Смирнова", "political", 10, [["Москва", 1]]),
            ("иван иванов", "Иван Иванов", "political", 400, []),
            ("александр беда", "Александр Беда", "criminal", 5, []),
        ):
            entity = EntityGroupRecord(
                key=key,
                name=name,
                variants=[[name, 1]],
                mention_count=1,
                article_count=1,
                event_types={},
                regions=regions,
                last_published_at=now - timedelta(days=days),
            )
            session.add(entity)
            session.flush()
            session.add(
                EntityGroupPoliticsRecord(
                    group_id=entity.id,
                    verdict=verdict,
                    method="model",
                    reason=f"так про {name}",
                    quote="цитата",
                )
            )
            if key == "иван иванов":
                session.flush()
                session.add(
                    EntityGroupRfMatchRecord(group_id=entity.id, entry_id=entry.id, level="name")
                )


def test_the_list_is_the_political_the_period_tells_new_from_old(
    session_factory: sessionmaker[Session],
) -> None:
    _seed(session_factory)

    with _client(session_factory) as client:
        page = client.get("/ui/political").text
        fresh = client.get("/ui/political", params={"months": 3}).text
        certain = client.get("/ui/political", params={"hide_maybe_listed": "true"}).text

    assert 'href="/ui/political">Список</a>' in page
    assert "Найдено: 2." in page and "Беда" not in page
    assert "Смирнова Анна" in page and "Москва" in page and "модель: так про Анна Смирнова" in page
    assert 'Иванов Иван</a> <span class="badge pending">возможно в перечне</span>' in page
    assert "Скрыть возможных в перечне (1)" in page
    # Иванов's latest news is a year old: not a new case.
    assert "Найдено: 1." in fresh and "Иванов" not in fresh
    assert "Найдено: 1." in certain and "Иванов Иван" not in certain


def test_the_excel_has_every_row_of_the_filters(session_factory: sessionmaker[Session]) -> None:
    _seed(session_factory)

    with _client(session_factory) as client:
        response = client.get("/ui/political/export.xlsx", params={"months": 0})

    assert response.status_code == 200
    sheet = load_workbook(BytesIO(response.content)).active
    assert sheet is not None
    rows = list(sheet.iter_rows(values_only=True))
    assert rows[0][:3] == ("№", "Фамилия Имя", "Регион")
    assert [(row[1], row[2], row[8]) for row in rows[1:]] == [
        ("Смирнова Анна", "Москва", None),
        ("Иванов Иван", None, "да"),
    ]
