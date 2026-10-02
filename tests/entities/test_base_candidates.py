"""Who in the operator's base an unnamed figurant may be."""

from __future__ import annotations

from datetime import UTC, date, datetime
from types import SimpleNamespace
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from db.orm_models import AirtableKnownPersonRecord
from entities.base_candidates import article_numbers, base_candidate_key, base_candidates
from entities.unnamed import DIFFERENT, SAME, SUPPLIED_NAME, decide, resolve_identity

NEWS = datetime(2026, 6, 11, tzinfo=UTC)


def _person(name: str, born: date, **values: Any) -> AirtableKnownPersonRecord:
    folded = name.lower()
    return AirtableKnownPersonRecord(
        external_id=f"rec-{folded}-{born}",
        full_name=name,
        normalized_name=folded,
        matching_key=folded.replace(" ", ""),
        birth_date=born,
        **{"gender": "female", "region": "Крым", "city": "Симферополь", **values},
    )


def _figurant(**values: Any) -> SimpleNamespace:
    return SimpleNamespace(
        **{
            "key": "k" * 64,
            "age": 27,
            "gender": "female",
            "place": "Крым",
            "initial": None,
            "articles": ["205"],
            "published_at": NEWS,
            **values,
        }
    )


def _seed(session_factory: sessionmaker[Session]) -> None:
    with session_factory.begin() as session:
        session.add_all(
            [
                _person(
                    "Иванова Мария Ивановна (Іванова Марія)",
                    date(1999, 1, 1),
                    articles="ст. 205 УК РФ ч. 1,ст. 222.1 УК РФ ч. 1",
                    sentenced_on=date(2024, 3, 1),
                ),
                # The same place and age, another article: shown, after her.
                # Her sentence is this very news, not an earlier one.
                _person(
                    "Абрамова Анна Олеговна",
                    date(1998, 12, 1),
                    articles="ст. 275 УК РФ",
                    sentenced_on=date(2026, 6, 11),
                ),
                # A year older than the text says: the event may be earlier than the news.
                _person("Белова Ольга Петровна", date(1998, 3, 1), articles="ст. 205 УК РФ ч. 2"),
                # Not her: too old, a man, another region, no birth date.
                _person("Старова Вера Ильинична", date(1990, 1, 1), articles="ст. 205 УК РФ"),
                _person("Петров Пётр Петрович", date(1999, 1, 1), gender="male"),
                _person(
                    "Омская Дарья Ильинична", date(1999, 1, 1), region="Омская область", city="Омск"
                ),
            ]
        )
        nameless = _person("Жительница Крыма 3", date(1999, 1, 1))
        nameless.birth_date = None
        session.add(nameless)


def test_the_people_of_that_age_sex_and_place_with_the_same_article_first(
    session_factory: sessionmaker[Session],
) -> None:
    _seed(session_factory)

    with session_factory() as session:
        found = base_candidates(session, _figurant())

    assert [item.name for item in found.shown] == [
        "Иванова Мария Ивановна",
        "Белова Ольга Петровна",
        "Абрамова Анна Олеговна",
    ]
    # Everyone of that age and sex, wherever from.
    assert found.total == 4
    first = found.shown[0]
    assert first.reasons == [
        "27 лет на 11.06.2026",
        "место: Крым, Симферополь",
        "та же статья: 205",
        "в базе уже есть приговор от 01.03.2024",
    ]
    assert first.key == "base:иванова мария ивановна (іванова марія)|1999-01-01"
    assert found.shown[1].age == 28 and found.shown[2].reasons == [
        "27 лет на 11.06.2026",
        "место: Крым, Симферополь",
    ]


def test_the_city_of_the_base_is_a_place_too(session_factory: sessionmaker[Session]) -> None:
    with session_factory.begin() as session:
        session.add(
            _person(
                "Будников Евгений Анатольевич",
                date(1975, 10, 9),
                gender="male",
                region="Алтайский край",
                city="Рубцовск",
                articles="ст. 205.1 УК РФ ч. 1.1",
            )
        )

    with session_factory() as session:
        found = base_candidates(
            session, _figurant(age=50, gender="male", place="Рубцовск", articles=["205.1"])
        )
        elsewhere = base_candidates(
            session, _figurant(age=50, gender="male", place="Барнаул", articles=["205.1"])
        )
        no_place = base_candidates(session, _figurant(age=50, gender="male", place=""))
        no_age = base_candidates(session, _figurant(age=None, gender="male", place="Рубцовск"))

    assert [item.name for item in found.shown] == ["Будников Евгений Анатольевич"]
    assert found.shown[0].article_match
    # The age and sex alone show nobody, only their number.
    assert (elsewhere.shown, elsewhere.total) == ([], 1)
    assert (no_place.shown, no_place.total) == ([], 1)
    assert (no_age.shown, no_age.total) == ([], 0)


def test_the_initial_and_an_unknown_sex(session_factory: sessionmaker[Session]) -> None:
    _seed(session_factory)
    with session_factory.begin() as session:
        session.add(_person("Иваненко Саша", date(1999, 2, 2), gender=None))

    with session_factory() as session:
        found = base_candidates(session, _figurant(initial="и"))

    # The base does not say Иваненко's sex: not a reason to hide them.
    assert [item.name for item in found.shown] == ["Иванова Мария Ивановна", "Иваненко Саша"]
    assert found.total == 2


def test_a_person_s_word_orders_the_candidates(session_factory: sessionmaker[Session]) -> None:
    _seed(session_factory)

    with session_factory.begin() as session:
        decide(
            session,
            "k" * 64,
            base_candidate_key("иванова мария ивановна (іванова марія)", date(1999, 1, 1)),
            DIFFERENT,
        )
        resolve_identity(session, "k" * 64, SUPPLIED_NAME, normalized_name="Абрамова Анна Олеговна")
    with session_factory() as session:
        found = base_candidates(session, _figurant())
        two = base_candidates(session, _figurant(), shown=2)

    assert [(item.name, item.decision) for item in found.shown] == [
        ("Абрамова Анна Олеговна", SAME),
        ("Белова Ольга Петровна", None),
        ("Иванова Мария Ивановна", DIFFERENT),
    ]
    assert [item.name for item in two.shown] == ["Абрамова Анна Олеговна", "Белова Ольга Петровна"]


def test_article_numbers_are_read_from_the_base_s_cell() -> None:
    assert article_numbers("ст. 205.1 УК РФ ч. 1,ст. 30 УК РФ ч. 3, ст.275 УК РФ") == {
        "205.1",
        "30",
        "275",
    }
    assert article_numbers("Нет информации") == set()
