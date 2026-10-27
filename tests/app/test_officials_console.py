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

from processing.storage.models import (
    EntityGroupRecord,
    EntityGroupRoleRecord,
    EntityOfficialMarkRecord,
    EntityPairDecisionRecord,
    ExcludedPersonRecord,
)
from web.app import app
from web.dependencies import get_db, get_operation_registry


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
        listed = client.get("/api/v1/officials")

    assert listed.status_code == 200
    assert listed.json()["total"] == 0 and listed.json()["rows"] == []


def test_a_person_is_added_from_the_console(session_factory: sessionmaker[Session]) -> None:
    with _client(session_factory) as client:
        answer = client.post(
            "/api/v1/officials/add",
            json={"full_name": "  Ольга   Минакова ", "category": "judge", "reason": "судья"},
        )

    assert answer.status_code == 200 and answer.json()["status"] == "added"
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
        client.post("/api/v1/officials/add", json={"full_name": "Алёна Петрова"})

    assert _rows(session_factory)[0].normalized_name == "алена петрова"


def test_the_same_person_twice_is_one_row(session_factory: sessionmaker[Session]) -> None:
    with _client(session_factory) as client:
        for _ in range(2):
            client.post("/api/v1/officials/add", json={"full_name": "Ольга Минакова"})

    assert len(_rows(session_factory)) == 1


def test_an_empty_name_is_refused(session_factory: sessionmaker[Session]) -> None:
    with _client(session_factory) as client:
        answer = client.post("/api/v1/officials/add", json={"full_name": "   "})

    assert answer.status_code == 400
    assert _rows(session_factory) == []


def test_an_unknown_category_falls_back_rather_than_being_stored(
    session_factory: sessionmaker[Session],
) -> None:
    with _client(session_factory) as client:
        client.post(
            "/api/v1/officials/add",
            json={"full_name": "Кто-то", "category": "выдумка"},
        )

    assert _rows(session_factory)[0].category == "other"


def test_taking_a_person_off_keeps_the_row(session_factory: sessionmaker[Session]) -> None:
    """«We looked and he is not an official» is an answer. A list that cannot remember
    it will be asked the same question again, and this time by someone else."""
    with _client(session_factory) as client:
        client.post("/api/v1/officials/add", json={"full_name": "Ольга Минакова"})
        client.post("/api/v1/officials/deactivate", json={"external_id": "console:ольга минакова"})

    rows = _rows(session_factory)
    assert len(rows) == 1
    assert rows[0].active is False


def test_taking_off_someone_who_is_not_there_is_a_404(
    session_factory: sessionmaker[Session],
) -> None:
    with _client(session_factory) as client:
        answer = client.post("/api/v1/officials/deactivate", json={"external_id": "console:никто"})

    assert answer.status_code == 404


def test_the_page_says_which_name_the_system_found(session_factory: sessionmaker[Session]) -> None:
    _entity(session_factory, "ольга минакова", "Ольга Минакова")
    with _client(session_factory) as client:
        client.post("/api/v1/officials/add", json={"full_name": "Ольга Минакова"})
        (row,) = client.get("/api/v1/officials").json()["rows"]

    assert (row["entity_key"], row["entity_name"]) == ("ольга минакова", "Ольга Минакова")


def test_a_name_nobody_matches_says_so_rather_than_pretending(
    session_factory: sessionmaker[Session],
) -> None:
    """The row is in the list and does not yet apply. Saying so is the whole point: a page
    that claimed the system will treat the person as an official would be lying."""
    with _client(session_factory) as client:
        client.post("/api/v1/officials/add", json={"full_name": "Никого Нет"})
        (row,) = client.get("/api/v1/officials").json()["rows"]

    # In the list, and said to apply to nobody yet: no person the name landed on.
    assert row["full_name"] == "Никого Нет" and row["entity_key"] == "" and row["active"]


def test_the_list_can_be_taken_away_as_a_file(session_factory: sessionmaker[Session]) -> None:
    with _client(session_factory) as client:
        client.post(
            "/api/v1/officials/add", json={"full_name": "Ольга Минакова", "category": "judge"}
        )
        client.post("/api/v1/officials/add", json={"full_name": "Пётр Судья"})
        client.post("/api/v1/officials/deactivate", json={"external_id": "console:пётр судья"})
        answer = client.get("/ui/airtable/officials.csv")

    assert answer.status_code == 200
    assert "attachment" in answer.headers["content-disposition"]
    lines = [line for line in answer.text.lstrip("﻿").splitlines() if line]
    assert lines[0] == "ФИО,Категория,Причина,В силе"
    assert "Ольга Минакова,judge,,да" in lines
    # The whole list, not only the page on screen.
    assert "Пётр Судья,other,,нет" in lines
    assert len(lines) == 3


def _role(
    session_factory: sessionmaker[Session], key: str, kind: str, method: str, reason: str = "почему"
) -> None:
    with session_factory.begin() as session:
        group_id = session.scalar(select(EntityGroupRecord.id).where(EntityGroupRecord.key == key))
        session.add(
            EntityGroupRoleRecord(
                group_id=group_id,
                role="mentioned",
                kind=kind,
                method=method,
                reason=reason,
                quote="",
            )
        )


def _suggest_world(session_factory: sessionmaker[Session]) -> None:
    """Five people the model or the rules call officials, one way or another."""
    for key, name in (
        ("дмитрий песков", "Дмитрий Песков"),  # the model's guess: offered
        ("ольга минакова", "Ольга Минакова"),  # by title: the rules' business, not offered
        ("игорь краснов", "Игорь Краснов"),  # the model's guess, but on the list: not offered
        ("марко рубио", "Марко Рубио"),  # the model's guess, a person said «no»: not offered
        ("иван иванов", "Иван <b>Иванов</b>"),  # a figurant: never offered
    ):
        _entity(session_factory, key, name)
    _role(session_factory, "дмитрий песков", "official", "model", "Представитель Кремля")
    _role(session_factory, "ольга минакова", "judge", "official")
    _role(session_factory, "игорь краснов", "official", "model")
    _role(session_factory, "марко рубио", "official", "model")
    _role(session_factory, "иван иванов", "accused", "model")
    with session_factory.begin() as session:
        session.add(
            ExcludedPersonRecord(
                external_id="console:краснов игорь",
                full_name="Краснов Игорь",
                normalized_name="краснов игорь",
                category="official",
                active=False,
            )
        )
        session.add(EntityOfficialMarkRecord(key="марко рубио", official=False))


def test_only_what_the_model_alone_calls_an_official_is_offered(
    session_factory: sessionmaker[Session],
) -> None:
    _suggest_world(session_factory)

    with _client(session_factory) as client:
        suggestions = client.get("/api/v1/officials").json()["suggestions"]

    assert [(item["key"], item["name"], item["reason"]) for item in suggestions] == [
        ("дмитрий песков", "Дмитрий Песков", "Представитель Кремля")
    ]


def test_accepting_a_suggestion_puts_the_person_on_the_list_once(
    session_factory: sessionmaker[Session],
) -> None:
    _suggest_world(session_factory)

    with _client(session_factory) as client:
        first = client.post(
            "/api/v1/officials/add-suggested",
            json={"key": "дмитрий песков"},
        )
        again = client.post("/api/v1/officials/add-suggested", json={"key": "дмитрий песков"})
        listed = client.get("/api/v1/officials").json()

    assert first.status_code == 200 and again.status_code == 200
    assert (first.json()["status"], again.json()["status"]) == ("added", "already")
    rows = [row for row in _rows(session_factory) if row.external_id.startswith("model:")]
    assert [(row.full_name, row.category, row.active) for row in rows] == [
        ("Дмитрий Песков", "official", True)
    ]
    assert rows[0].reason == "предложено моделью, принято вручную: Представитель Кремля"
    # No longer a suggestion; now a row of the list.
    assert listed["suggestions"] == []
    assert "Дмитрий Песков" in [row["full_name"] for row in listed["rows"]]


def test_a_key_that_is_no_suggestion_is_refused(session_factory: sessionmaker[Session]) -> None:
    _suggest_world(session_factory)

    with _client(session_factory) as client:
        nobody = client.post("/api/v1/officials/add-suggested", json={"key": "никто такой"})
        by_title = client.post("/api/v1/officials/add-suggested", json={"key": "ольга минакова"})
        accused = client.post("/api/v1/officials/add-suggested", json={"key": "иван иванов"})
        empty = client.post("/api/v1/officials/add-suggested", json={})

    assert (nobody.status_code, by_title.status_code, accused.status_code) == (404, 404, 404)
    # Nothing named: the request itself is refused.
    assert empty.status_code == 422
    assert not [row for row in _rows(session_factory) if row.external_id.startswith("model:")]


def test_a_suggested_name_is_given_as_written(session_factory: sessionmaker[Session]) -> None:
    """The page escapes what it shows; the API gives the name and the reason as they are."""
    _entity(session_factory, "x", "Ёж <script>alert(1)</script>")
    _role(session_factory, "x", "official", "model", "<b>почему</b>")

    with _client(session_factory) as client:
        (suggestion,) = client.get("/api/v1/officials").json()["suggestions"]

    assert (suggestion["name"], suggestion["reason"]) == (
        "Ёж <script>alert(1)</script>",
        "<b>почему</b>",
    )


def test_the_api_reads_and_changes_the_list_the_page_does(
    session_factory: sessionmaker[Session],
) -> None:
    """`/api/v1/officials` for the React page: the same rows, suggestions and words."""
    _entity(session_factory, "ольга минакова", "Ольга Минакова")

    with _client(session_factory) as client:
        empty = client.get("/api/v1/officials").json()
        added = client.post(
            "/api/v1/officials/add",
            json={"full_name": "  Ольга   Минакова ", "category": "judge", "reason": "судья"},
        ).json()
        again = client.post("/api/v1/officials/add", json={"full_name": "Ольга Минакова"}).json()
        nameless = client.post("/api/v1/officials/add", json={"full_name": "  "})
        listed = client.get("/api/v1/officials").json()
        foreign = client.post(
            "/api/v1/officials/deactivate",
            json={"external_id": "console:ольга минакова"},
            headers={"Origin": "https://evil.example"},
        )
        off = client.post(
            "/api/v1/officials/deactivate", json={"external_id": "console:ольга минакова"}
        ).json()
        nobody = client.post("/api/v1/officials/deactivate", json={"external_id": "x"})
        nothing = client.post("/api/v1/officials/add-suggested", json={"key": "ольга минакова"})

    assert empty["total"] == 0 and empty["rows"] == []
    assert {"value": "judge", "label": "судья", "count": None} in empty["categories"]
    assert added == {"status": "added"} and again == {"status": "already"}
    assert nameless.status_code == 400
    row = listed["rows"][0]
    assert row["full_name"] == "Ольга Минакова" and row["category"] == "судья"
    assert row["entity_key"] == "ольга минакова" and row["active"] is True
    assert foreign.status_code == 403
    assert off == {"status": "deactivated"} and _rows(session_factory)[0].active is False
    assert nobody.status_code == 404
    assert nothing.status_code == 404 and nothing.json()["detail"] == "Такого предложения нет"


def test_a_suggestion_names_the_entity_it_may_be_spelled_as(
    session_factory: sessionmaker[Session],
) -> None:
    """«Эммануэль» and «Эмманюэль» Макрон: the suggestion says so; a pair decided
    «different» in the review no longer does."""
    _entity(session_factory, "эммануэль макрон", "Эммануэль Макрон")
    _entity(session_factory, "эмманюэль макрон", "Эмманюэль Макрон")
    _entity(session_factory, "ольга минакова", "Ольга Минакова")
    _role(session_factory, "эммануэль макрон", "official", "model")
    _role(session_factory, "ольга минакова", "judge", "model")

    with _client(session_factory) as client:
        before = client.get("/api/v1/officials").json()["suggestions"]
        with session_factory.begin() as session:
            session.add(
                EntityPairDecisionRecord(
                    key_a="эммануэль макрон",
                    key_b="эмманюэль макрон",
                    decision="different",
                    source="manual",
                )
            )
        after = client.get("/api/v1/officials").json()["suggestions"]

    assert {row["name"]: row["possible_duplicates"] for row in before} == {
        "Эммануэль Макрон": ["Эмманюэль Макрон"],
        "Ольга Минакова": [],
    }
    assert [row["possible_duplicates"] for row in after] == [[], []]


def test_a_name_with_a_patronymic_and_the_same_with_a_surname_are_named_to_each_other(
    session_factory: sessionmaker[Session],
) -> None:
    """«Алексей Михайлович» has the patronymic where a surname stands, so the pair review
    never pairs it with «Алексей Михайлович Становский». The suggestion says so; a name
    with another patronymic or nobody to pair with is left alone."""
    for key, name in (
        ("алексей михайлович", "Алексей Михайлович"),
        ("алексей михайлович становский", "Алексей Михайлович Становский"),
        ("алексей петрович становский", "Алексей Петрович Становский"),
        ("василий викторович", "Василий Викторович"),
    ):
        _entity(session_factory, key, name)
        _role(session_factory, key, "judge", "model")

    with _client(session_factory) as client:
        before = client.get("/api/v1/officials").json()["suggestions"]
        with session_factory.begin() as session:
            session.add(
                EntityPairDecisionRecord(
                    key_a="алексей михайлович",
                    key_b="алексей михайлович становский",
                    decision="different",
                    source="manual",
                )
            )
        after = client.get("/api/v1/officials").json()["suggestions"]

    assert {row["name"]: row["possible_duplicates"] for row in before} == {
        "Алексей Михайлович": ["Алексей Михайлович Становский"],
        "Алексей Михайлович Становский": ["Алексей Михайлович"],
        "Алексей Петрович Становский": [],
        "Василий Викторович": [],
    }
    assert [row["possible_duplicates"] for row in after] == [[], [], [], []]


def test_a_decision_made_before_the_surname_was_cut_another_way_still_removes_the_mark(
    session_factory: sessionmaker[Session],
) -> None:
    """«навальный» became «навальн»: the pair review reads the old decision in today's keys,
    and so must the mark beside the suggestion."""
    for key, name in (
        ("максим павлович", "Максим Павлович"),
        ("максим павлович навальн", "Максим Павлович Навальн"),
    ):
        _entity(session_factory, key, name)
        _role(session_factory, key, "judge", "model")

    with _client(session_factory) as client:
        before = client.get("/api/v1/officials").json()["suggestions"]
        with session_factory.begin() as session:
            session.add(
                EntityPairDecisionRecord(
                    key_a="максим павлович",
                    key_b="максим павлович навальный",
                    decision="different",
                    source="manual",
                )
            )
        after = client.get("/api/v1/officials").json()["suggestions"]

    assert [row["possible_duplicates"] for row in before] != [[], []]
    assert [row["possible_duplicates"] for row in after] == [[], []]
