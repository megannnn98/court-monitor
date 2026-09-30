"""The officials list in step 4: a name a person wrote down is never a target."""

from __future__ import annotations

from sqlalchemy.orm import Session, sessionmaker

from airtable.client import AirtableRecord
from db.orm_models import ExcludedPersonRecord
from entities.officials import official_entity_ids


def _official(session: Session, name: str, **fields: object) -> ExcludedPersonRecord:
    record = ExcludedPersonRecord(
        external_id=str(fields.get("external_id", f"rec-{name}")),
        full_name=name,
        normalized_name=name,
        category=str(fields.get("category", "judge")),
        reason=fields.get("reason"),  # type: ignore[arg-type]
        active=bool(fields.get("active", True)),
    )
    session.add(record)
    return record


def test_an_exclusion_finds_the_entity_whatever_order_the_name_is_written(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as session:
        _official(session, "Минакова Ольга Викторовна")

    # An entity key reads the given name first; the list a person keeps writes the
    # surname first. Both must find the same person.
    keys = {1: "ольга минакова"}
    with session_factory() as session:
        assert set(official_entity_ids(session, keys)) == {1}


def test_a_name_in_the_case_of_the_key_is_found(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as session:
        _official(session, "Иванов Иван Иванович")

    keys = {7: "иван иванович иванов"}
    with session_factory() as session:
        assert set(official_entity_ids(session, keys)) == {7}


def test_a_deactivated_exclusion_stops_matching(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as session:
        _official(session, "Минакова Ольга Викторовна", active=False)

    with session_factory() as session:
        assert official_entity_ids(session, {1: "ольга минакова"}) == {}


def test_a_person_nobody_mentions_is_simply_left_out(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as session:
        _official(session, "Неизвестный Человек")

    with session_factory() as session:
        assert official_entity_ids(session, {1: "ольга минакова"}) == {}


def test_a_shorter_key_still_resolves_through_the_stem(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as session:
        _official(session, "Дудь Елена Фёдоровна")

    # The key carries the surname's stem, as the rules cut it: «Дудь» → «дуд».
    keys = {3: "елена федоровна дуд"}
    with session_factory() as session:
        assert set(official_entity_ids(session, keys)) == {3}


def test_the_category_and_reason_come_through(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as session:
        _official(
            session,
            "Александр Бастрыкин",
            category="police",
            reason="глава СК",
        )

    with session_factory() as session:
        found = official_entity_ids(session, {1: "александр бастрыкин"})[1]
    assert (found.category, found.reason) == ("police", "глава СК")


def test_the_exclusion_list_is_empty_by_default(session_factory: sessionmaker[Session]) -> None:
    with session_factory() as session:
        assert official_entity_ids(session, {1: "ольга минакова"}) == {}


def test_a_name_that_could_be_two_people_excludes_neither(
    session_factory: sessionmaker[Session],
) -> None:
    """Two entities whose keys the same words could stand for. Guessing which one the
    operator meant is worse than not excluding either, so the row is left out."""
    with session_factory.begin() as session:
        _official(session, "Иванов Иван")

    keys = {1: "иванов иван", 2: "иван иванов"}
    with session_factory() as session:
        assert official_entity_ids(session, keys) == {}


def test_a_bare_surname_never_excludes_anybody(
    session_factory: sessionmaker[Session],
) -> None:
    """«Иванов» is half the city: a single word is never matched on."""
    with session_factory.begin() as session:
        _official(session, "Иванов")

    keys = {1: "иван иванов", 2: "ольга иванова"}
    with session_factory() as session:
        assert official_entity_ids(session, keys) == {}


def test_a_sync_is_what_fills_the_list(session_factory: sessionmaker[Session]) -> None:
    """The list is written by the sync, so its rows carry an Airtable record id."""
    from airtable.repository import sync_officials

    records = [AirtableRecord("recA", {"full_name": "Минакова Ольга Викторовна"})]
    with session_factory.begin() as session:
        result = sync_officials(session, records)
    assert result.created == 1
    with session_factory() as session:
        row = session.query(ExcludedPersonRecord).one()
        assert row.external_id == "recA"
