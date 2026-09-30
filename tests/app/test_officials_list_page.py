"""The officials list in the console: added, looked at, downloaded, switched off.

What matters here is not that the buttons exist but what they refuse to do. The list
decides who is never a target, so a name that fits nobody, a name added twice, and a
person switched off rather than deleted each have one right behaviour, and these tests
are that behaviour.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from db.orm_models import EntityGroupRecord, ExcludedPersonRecord
from web.app import app
from web.dependencies import get_db
from web.routers.operations import get_operation_registry


@contextmanager
def _client(session_factory: sessionmaker[Session]) -> Iterator[TestClient]:
    def override_get_db() -> Iterator[Session]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_operation_registry] = lambda: None
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_operation_registry, None)


def _entity(session_factory: sessionmaker[Session], key: str, name: str) -> None:
    with session_factory.begin() as session:
        session.add(
            EntityGroupRecord(
                key=key,
                name=name,
                variants=[],
                mention_count=5,
                article_count=1,
                event_types={},
                regions=[],
            )
        )


def _rows(session_factory: sessionmaker[Session]) -> list[ExcludedPersonRecord]:
    with session_factory() as session:
        return list(
            session.scalars(select(ExcludedPersonRecord).order_by(ExcludedPersonRecord.full_name))
        )


def test_the_list_can_be_read_before_it_has_anybody(session_factory: sessionmaker[Session]) -> None:
    with _client(session_factory) as client:
        page = client.get("/ui/airtable/officials")

    assert page.status_code == 200
    assert "Список пуст" in page.text


def test_a_person_is_added_from_the_console(session_factory: sessionmaker[Session]) -> None:
    with _client(session_factory) as client:
        answer = client.post(
            "/ui/airtable/officials/add",
            data={"full_name": "  Ольга   Минакова ", "category": "judge", "reason": "судья"},
            follow_redirects=False,
        )

    assert answer.status_code == 303
    rows = _rows(session_factory)
    assert len(rows) == 1
    assert rows[0].full_name == "Ольга Минакова"
    assert rows[0].category == "judge"
    assert rows[0].active is True


def test_the_name_is_folded_the_way_the_pipeline_folds_it(
    session_factory: sessionmaker[Session],
) -> None:
    """Step 4 matches the folded name; a row stored unfolded would match nobody."""
    with _client(session_factory) as client:
        client.post("/ui/airtable/officials/add", data={"full_name": "Алёна Петрова"})

    assert _rows(session_factory)[0].normalized_name == "алена петрова"


def test_the_same_person_twice_is_one_row(session_factory: sessionmaker[Session]) -> None:
    with _client(session_factory) as client:
        for _ in range(2):
            client.post("/ui/airtable/officials/add", data={"full_name": "Ольга Минакова"})

    assert len(_rows(session_factory)) == 1


def test_an_empty_name_is_refused(session_factory: sessionmaker[Session]) -> None:
    with _client(session_factory) as client:
        answer = client.post("/ui/airtable/officials/add", data={"full_name": "   "})

    assert answer.status_code == 400
    assert _rows(session_factory) == []


def test_an_unknown_category_falls_back_rather_than_being_stored(
    session_factory: sessionmaker[Session],
) -> None:
    with _client(session_factory) as client:
        client.post(
            "/ui/airtable/officials/add",
            data={"full_name": "Кто-то", "category": "выдумка"},
        )

    assert _rows(session_factory)[0].category == "other"


def test_taking_a_person_off_keeps_the_row(session_factory: sessionmaker[Session]) -> None:
    """«We looked and he is not an official» is an answer. A list that cannot remember
    it will be asked the same question again, and this time by someone else."""
    with _client(session_factory) as client:
        client.post("/ui/airtable/officials/add", data={"full_name": "Ольга Минакова"})
        client.post(
            "/ui/airtable/officials/deactivate", data={"external_id": "console:ольга минакова"}
        )

    rows = _rows(session_factory)
    assert len(rows) == 1
    assert rows[0].active is False


def test_taking_off_someone_who_is_not_there_is_a_404(
    session_factory: sessionmaker[Session],
) -> None:
    with _client(session_factory) as client:
        answer = client.post(
            "/ui/airtable/officials/deactivate", data={"external_id": "console:никто"}
        )

    assert answer.status_code == 404


def test_the_page_says_which_name_the_system_found(session_factory: sessionmaker[Session]) -> None:
    _entity(session_factory, "ольга минакова", "Ольга Минакова")
    with _client(session_factory) as client:
        client.post("/ui/airtable/officials/add", data={"full_name": "Ольга Минакова"})
        page = client.get("/ui/airtable/officials").text

    assert "Ольга Минакова</a>" in page


def test_a_name_nobody_matches_says_so_rather_than_pretending(
    session_factory: sessionmaker[Session],
) -> None:
    """The row is in the list and does not yet apply. Saying so is the whole point: a page
    that claimed the system will treat the person as an official would be lying."""
    with _client(session_factory) as client:
        client.post("/ui/airtable/officials/add", data={"full_name": "Никого Нет"})
        page = client.get("/ui/airtable/officials").text

    assert "ещё не встречался в статьях" in page


def test_the_list_can_be_taken_away_as_a_file(session_factory: sessionmaker[Session]) -> None:
    with _client(session_factory) as client:
        client.post(
            "/ui/airtable/officials/add", data={"full_name": "Ольга Минакова", "category": "judge"}
        )
        client.post("/ui/airtable/officials/add", data={"full_name": "Пётр Судья"})
        client.post("/ui/airtable/officials/deactivate", data={"external_id": "console:пётр судья"})
        answer = client.get("/ui/airtable/officials.csv")

    assert answer.status_code == 200
    assert "attachment" in answer.headers["content-disposition"]
    lines = [line for line in answer.text.lstrip("﻿").splitlines() if line]
    assert lines[0] == "ФИО,Категория,Причина,В силе"
    assert "Ольга Минакова,judge,,да" in lines
    # The whole list, not only the page on screen.
    assert "Пётр Судья,other,,нет" in lines
    assert len(lines) == 3
