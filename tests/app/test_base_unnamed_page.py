"""«Без имени в базе»: nameless records of the base, their candidates, a person's word."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from db.orm_models import (
    AirtableKnownPersonRecord,
    RosfinmonitoringEntryRecord,
    RosfinmonitoringSnapshotRecord,
)
from web.app import app
from web.dependencies import get_db

KEY = "будников <евгений>|1975-10-09"


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
                external_id="share:known:1",
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


def _decide(client: TestClient, decision: str, **values: str) -> int:
    return client.post(
        "/ui/base-unnamed/decide",
        data={
            "record": "share:known:1",
            "candidate": KEY,
            "decision": decision,
            "back": "status=open&page=1",
            **values,
        },
        follow_redirects=False,
    ).status_code


def test_nothing_to_identify_says_what_is_needed(session_factory: sessionmaker[Session]) -> None:
    with _client(session_factory) as client:
        page = client.get("/ui/base-unnamed").text

    assert "Записей без имени с кандидатами из перечня нет." in page
    assert "Не разобраны (0)" in page


def test_a_card_shows_the_record_and_takes_a_word(session_factory: sessionmaker[Session]) -> None:
    _seed(session_factory)

    with _client(session_factory) as client:
        page = client.get("/ui/base-unnamed").text
        linked = client.get("/ui/unnamed").text
        same = client.post(
            "/ui/base-unnamed/decide",
            data={
                "record": "share:known:1",
                "candidate": KEY,
                "decision": "same",
                "back": "status=open&page=1",
            },
            follow_redirects=False,
        )
        still_open = client.get("/ui/base-unnamed").text
        found = client.get("/ui/base-unnamed", params={"status": "found"}).text
        assert _decide(client, "different") == 303
        rejected = client.get("/ui/base-unnamed", params={"status": "all"}).text
        assert _decide(client, "clear") == 303
        cleared = client.get("/ui/base-unnamed").text
        assert _decide(client, "maybe") == 400
        assert _decide(client, "same", candidate="") == 400
        assert _decide(client, "same", record="nobody") == 400

    # Both the base and the list are escaped.
    assert "50-летний житель &lt;Рубцовска&gt;" in page and "<Рубцовска>" not in page
    assert "БУДНИКОВ &lt;ЕВГЕНИЙ&gt;</th>" in page and "<ЕВГЕНИЙ>" not in page
    assert (
        "50 лет · мужчина · Алтайский край, Рубцовск · дело возбуждено 09.06.2025 · "
        "ст. 205.1 УК РФ ч. 1.1"
    ) in page
    assert "<td>09.10.1975</td><td>Г. РУБЦОВСК АЛТАЙСКОГО КРАЯ</td>" in page
    assert "включён в перечень 14.10.2025, после возбуждения дела" in page
    assert "Не разобраны (1)" in page and "Опознаны (0)" in page
    assert 'name="candidate" value="будников &lt;евгений&gt;|1975-10-09"' in page
    assert 'href="/ui/base-unnamed"' in linked
    assert same.status_code == 303
    assert same.headers["location"] == "/ui/base-unnamed?status=open&page=1#b-share%3Aknown%3A1"
    assert "В этом разделе никого." in still_open
    assert "Опознаны (1)" in found and "опознан: БУДНИКОВ &lt;ЕВГЕНИЙ&gt;</span>" in found
    assert '<span class="badge succeeded">это он</span>' in found and ">Отменить<" in found
    assert "кандидаты отклонены" in rejected and '<span class="badge">не он</span>' in rejected
    assert "Не разобраны (0)" in rejected
    assert "Не разобраны (1)" in cleared and ">Отменить<" not in cleared
    with session_factory() as session:
        assert session.execute(text("SELECT count(*) FROM unnamed_decisions")).scalar() == 0
