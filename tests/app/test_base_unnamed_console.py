"""«Без имени в базе»: nameless records of the base, their candidates, a person's word.

Read and decided through `/api/v1/base-unnamed`, as the React page does."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from analysis.storage.models import (
    AirtableKnownPersonRecord,
    RosfinmonitoringEntryRecord,
    RosfinmonitoringSnapshotRecord,
)
from web.app import app
from web.dependencies import get_db

KEY = "будников <евгений>|1975-10-09"
API = "/api/v1/base-unnamed"
RECORD = "share:known:1"


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
    with session_factory.begin() as session:
        session.add(
            AirtableKnownPersonRecord(
                external_id=RECORD,
                full_name="50-летний житель <Рубцовска>",
                normalized_name="x",
                matching_key="x",
                gender="male",
                region="Алтайский край",
                city="Рубцовск",
                articles="ст. 205.1 УК РФ ч. 1.1",
                case_opened_on=date(2025, 6, 9),
            )
        )
        # People of the base already sentenced under the article in the region.
        for index, court in enumerate(["2-й Восточный <окружной> суд"] * 2 + ["Рубцовский суд"]):
            session.add(
                AirtableKnownPersonRecord(
                    external_id=f"sentenced{index}",
                    full_name=f"Осуждённый Номер {index}",
                    normalized_name="x",
                    matching_key="x",
                    region="Алтайский край",
                    articles="ст. 205.1 УК РФ ч. 1",
                    court=court,
                    court_card_url='https://2vovs.sudrf.test/card?"id"=1' if index == 0 else None,
                )
            )
        snapshot = RosfinmonitoringSnapshotRecord(
            snapshot_date=datetime(2026, 9, 1, tzinfo=UTC),
            source_url="https://fedsfm.test",
            content_hash="a",
            entry_count=1,
            fetched_at=datetime(2026, 9, 1, tzinfo=UTC),
        )
        session.add(snapshot)
        session.flush()
        session.add(
            RosfinmonitoringEntryRecord(
                snapshot_id=snapshot.id,
                full_name="БУДНИКОВ <ЕВГЕНИЙ>",
                normalized_name="будников <евгений>",
                matching_key="будниковевгений",
                birth_date=datetime(1975, 10, 9, tzinfo=UTC),
                birth_place="Г. РУБЦОВСК АЛТАЙСКОГО КРАЯ",
                inclusion_date=datetime(2025, 10, 14, tzinfo=UTC),
            )
        )


def _list(client: TestClient, status: str | None = None) -> dict[str, Any]:
    params = {} if status is None else {"status": status}
    listed: dict[str, Any] = client.get(API, params=params).json()
    return listed


def _counts(listed: dict[str, Any]) -> dict[str, int]:
    return {option["value"]: option["count"] for option in listed["statuses"]}


def _decide(client: TestClient, decision: str, **values: str) -> int:
    word = {"record": RECORD, "candidate": KEY, "decision": decision, **values}
    status: int = client.post(f"{API}/decide", json=word).status_code
    return status


def test_nothing_to_identify_says_what_is_needed(session_factory: sessionmaker[Session]) -> None:
    with _client(session_factory) as client:
        listed = _list(client)

    assert listed["none_yet"] is True and listed["items"] == []
    assert _counts(listed)["open"] == 0


def test_a_card_shows_the_record_and_takes_a_word(session_factory: sessionmaker[Session]) -> None:
    _seed(session_factory)

    with _client(session_factory) as client:
        opened = _list(client)
        assert _decide(client, "same") == 200
        still_open = _list(client)
        found = _list(client, "found")
        assert _decide(client, "different") == 200
        rejected = _list(client, "all")
        assert _decide(client, "clear") == 200
        cleared = _list(client)
        assert _decide(client, "maybe") == 400
        assert _decide(client, "same", candidate="") == 400
        assert _decide(client, "same", candidate="x" * 256) == 400
        assert _decide(client, "same", candidate="ушедший иван|1975-01-01") == 200
        gone = _list(client, "found")
        gone_open = _list(client)
        assert _decide(client, "clear", candidate="ушедший иван|1975-01-01") == 200
        assert _decide(client, "same", record="nobody") == 400

    # The base and the list, as written.
    (card,) = opened["items"]
    assert card["record"] == RECORD and card["state"] == "open"
    assert card["full_name"] == "50-летний житель <Рубцовска>"
    assert card["facts"] == (
        "50 лет · мужчина · Алтайский край, Рубцовск · дело возбуждено 09.06.2025 · "
        "ст. 205.1 УК РФ ч. 1.1"
    )
    (candidate,) = card["candidates"]
    assert candidate["key"] == KEY and candidate["full_name"] == "БУДНИКОВ <ЕВГЕНИЙ>"
    assert (candidate["birth_date"], candidate["birth_place"]) == (
        "1975-10-09",
        "Г. РУБЦОВСК АЛТАЙСКОГО КРАЯ",
    )
    assert "включён в перечень 14.10.2025, после возбуждения дела" in candidate["reasons"]
    assert candidate["decision"] is None
    # Where the article is tried from that place, with the court's own site.
    assert [(hint["court"], hint["cases"], hint["site"]) for hint in card["courts"]] == [
        ("2-й Восточный <окружной> суд", 2, "https://2vovs.sudrf.test"),
        ("Рубцовский суд", 1, None),
    ]
    assert card["courts_total"] == 3
    assert _counts(opened)["open"] == 1 and _counts(opened)["found"] == 0
    assert still_open["items"] == [] and still_open["none_yet"] is False
    (identified,) = found["items"]
    assert _counts(found)["found"] == 1
    assert identified["state"] == "identified"
    assert identified["identified_as"] == "БУДНИКОВ <ЕВГЕНИЙ>"
    assert identified["candidates"][0]["decision"] == "same"
    (refused,) = rejected["items"]
    assert refused["state"] == "rejected" and refused["candidates"][0]["decision"] == "different"
    assert _counts(rejected)["open"] == 0
    assert _counts(cleared)["open"] == 1 and cleared["items"][0]["state"] == "open"
    # A word on an entry that is not among the candidates is shown, not lost.
    (confirmed,) = gone["items"]
    assert _counts(gone)["found"] == 1
    assert confirmed["state"] == "confirmed" and confirmed["identified_as"] == "ушедший иван"
    assert confirmed["confirmed"] == "ушедший иван|1975-01-01"
    assert gone_open["items"] == []


def test_a_confirmed_record_without_candidates_keeps_its_word(
    session_factory: sessionmaker[Session],
) -> None:
    _seed(session_factory)
    with session_factory.begin() as session:
        # The age leaves the name: nothing to compare with the list any more.
        session.execute(
            text(
                "UPDATE airtable_known_persons SET full_name = 'Житель Рубцовска 3' "
                "WHERE external_id = 'share:known:1'"
            )
        )

    with _client(session_factory) as client:
        unconfirmed = _list(client, "all")
        assert _decide(client, "same") == 200
        found = _list(client, "found")
        assert _decide(client, "clear") == 200
        cleared = _list(client, "all")

    assert _counts(unconfirmed)["all"] == 0 and _counts(cleared)["all"] == 0
    (card,) = found["items"]
    assert _counts(found)["found"] == 1 and card["full_name"] == "Житель Рубцовска 3"
    assert card["state"] == "confirmed" and card["identified_as"] == "будников <евгений>"
    assert card["candidates"] == []
    assert card["facts"].startswith("мужчина · Алтайский край, Рубцовск")
    with session_factory() as session:
        assert session.execute(text("SELECT count(*) FROM unnamed_decisions")).scalar() == 0


def test_the_api_refuses_what_it_should(session_factory: sessionmaker[Session]) -> None:
    _seed(session_factory)
    word = {"record": RECORD, "candidate": KEY}

    with _client(session_factory) as client:
        same = client.post(f"{API}/decide", json={**word, "decision": "same"})
        wrong = client.post(f"{API}/decide", json={**word, "decision": "maybe"})
        foreign = client.post(
            f"{API}/decide",
            json={**word, "decision": "clear"},
            headers={"Origin": "https://evil.example"},
        )

    assert same.json() == {"record": RECORD}
    assert wrong.status_code == 400 and wrong.json()["detail"] == "Неполное решение"
    assert foreign.status_code == 403
