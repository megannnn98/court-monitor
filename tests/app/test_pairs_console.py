"""«Спорные случаи»: two entities side by side, and a person's decision."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from analysis.storage.models import (
    EntityGroupRfMatchRecord,
    RosfinmonitoringEntryRecord,
    RosfinmonitoringSnapshotRecord,
)
from processing.storage.models import EntityGroupRecord, EntityPairDecisionRecord
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


def _pairs(client: TestClient, **params: str) -> dict[str, Any]:
    """«Пары» as the React page reads them."""
    listed: dict[str, Any] = client.get("/api/v1/review/pairs", params=params).json()
    return listed


def _names(listed: dict[str, Any]) -> list[tuple[str, str]]:
    return [(item["left"]["name"], item["right"]["name"]) for item in listed["items"]]


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
        listed = _pairs(client)
        similar = _pairs(client, kind="similar")

    assert listed["total"] == 2
    kinds = {option["value"]: (option["label"], option["count"]) for option in listed["kinds"]}
    assert kinds["patronymic"] == ("С отчеством и без", 1) and kinds["similar"] == (
        "Похожее имя",
        1,
    )
    # Side by side: the shorter name on the left.
    assert ("Ранав Игорь", "Ранав Игорь Александрович") in _names(listed)
    ranav = next(item for item in listed["items"] if item["left"]["name"] == "Ранав Игорь")
    assert "Чукотский автономный округ" in ranav["left"]["regions"] + ranav["right"]["regions"]
    assert similar["total"] == 1 and _names(similar)[0][0] == "Мониава Лида"


def test_one_person_merges_and_different_people_leave_the_list(
    session_factory: sessionmaker[Session],
) -> None:
    _entities(session_factory, *PEOPLE)

    with _client(session_factory) as client:
        same = client.post(
            "/api/v1/review/pairs/decide",
            json={"key_a": "игорь ранав", "key_b": "игорь александрович ранав", "decision": "same"},
        )
        client.post(
            "/api/v1/review/pairs/decide",
            json={"key_a": "лида мониава", "key_b": "лидия мониава", "decision": "different"},
        )
        listed = _pairs(client)
        broken = client.post(
            "/api/v1/review/pairs/decide",
            json={"key_a": "лида мониава", "key_b": "", "decision": "same"},
        )

    assert same.status_code == 200
    assert listed["total"] == 0 and listed["open_pairs"] == 0
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
        pairs = _pairs(client)

    assert pairs["total"] == 3
    notes = [item["note"] for item in pairs["items"] if item["note"]]
    # Both pairs of «Денис Попов» say why; only the listed one says it is on the list.
    assert len(notes) == 2 and all(note.startswith("Не слито автоматически") for note in notes)
    assert sum("хотя одна сторона в перечне" in note for note in notes) == 1
    assert all("«Попов Денис» подходит сразу к нескольким людям" in note for note in notes)
    # The card's region, as the card wrote it, tells namesakes of one full name apart.
    assert any(
        "«Попов Денис Владимирович» (Курская область), «Попов Денис Александрович»" in note
        for note in notes
    )


def test_a_name_and_a_patronymic_is_not_the_reason_a_clear_pair_waits(
    session_factory: sessionmaker[Session],
) -> None:
    """«максим кисель» is clearly «максим павлович кисель»: the check merges it. The bare
    «максим павлович» is a pair for a person, and is not said to hold the clear one back."""
    _entities(
        session_factory,
        ("максим павлович", "Максим Павлович", 3),
        ("максим павлович кисель", "Максим Павлович Кисель", 2),
        ("максим кисель", "Максим Кисель", 1),
    )

    with _client(session_factory) as client:
        listed = _pairs(client)
        own = _pairs(client, kind="name_patronymic")

    assert listed["total"] == 2
    assert all(item["note"] is None for item in listed["items"])
    assert own["total"] == 1 and _names(own) == [("Павлович Максим", "Кисель Максим Павлович")]
