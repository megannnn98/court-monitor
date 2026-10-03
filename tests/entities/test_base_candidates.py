"""Who in the operator's base an unnamed figurant may be."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
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
        resolve_identity(
            session,
            "k" * 64,
            SUPPLIED_NAME,
            normalized_name="Абрамова Анна Олеговна",
            rf_birth_date=date(1998, 12, 1),
        )
    with session_factory() as session:
        found = base_candidates(session, _figurant())
        two = base_candidates(session, _figurant(), shown=2)

    assert [(item.name, item.decision) for item in found.shown] == [
        ("Абрамова Анна Олеговна", SAME),
        ("Белова Ольга Петровна", None),
        ("Иванова Мария Ивановна", DIFFERENT),
    ]
    assert [item.name for item in two.shown] == ["Абрамова Анна Олеговна", "Белова Ольга Петровна"]


def test_the_word_is_on_one_person_not_on_a_name(session_factory: sessionmaker[Session]) -> None:
    with session_factory.begin() as session:
        session.add_all(
            [
                _person("Иванова Мария Ивановна", date(1999, 1, 1)),
                # A namesake, of the same age and place.
                _person("Иванова Мария Ивановна", date(1998, 9, 9)),
            ]
        )
        resolve_identity(
            session,
            "k" * 64,
            SUPPLIED_NAME,
            normalized_name="Иванова Мария Ивановна",
            rf_birth_date=date(1998, 9, 9),
        )
    with session_factory() as session:
        found = base_candidates(session, _figurant())
    assert [(item.birth_date, item.decision) for item in found.shown] == [
        (date(1998, 9, 9), SAME),
        (date(1999, 1, 1), None),
    ]

    # A name typed by hand carries no birth date: it confirms nobody of the base.
    with session_factory.begin() as session:
        resolve_identity(session, "k" * 64, SUPPLIED_NAME, normalized_name="Иванова Мария Ивановна")
    with session_factory() as session:
        typed = base_candidates(session, _figurant())
    assert [item.decision for item in typed.shown] == [None, None]


def test_a_place_is_found_where_a_word_begins(session_factory: sessionmaker[Session]) -> None:
    born = date(1999, 1, 1)
    with session_factory.begin() as session:
        session.add_all(
            [
                _person("Омская Анна Ильинична", born, region="Омская область", city="Омск"),
                _person("Томская Анна Ильинична", born, region="Томская область", city="Томск"),
                _person("Уфимская Анна Ильинична", born, region="Башкортостан", city="Уфа"),
                _person("Подольская Анна Ильинична", born, region="Подмосковье", city="Подольск"),
            ]
        )

    with session_factory() as session:
        omsk = base_candidates(session, _figurant(place="Омск"))
        ufa = base_candidates(session, _figurant(place="Уфа"))
        # «под» is no place.
        near = base_candidates(session, _figurant(place="под Омском"))

    assert [item.name for item in omsk.shown] == ["Омская Анна Ильинична"]
    assert [item.name for item in ufa.shown] == ["Уфимская Анна Ильинична"]
    assert [item.name for item in near.shown] == ["Омская Анна Ильинична"]


def _sentenced(session_factory: sessionmaker[Session]) -> None:
    """«53-летняя жительница Якутии» of the news is a woman of the base whose case was
    opened in Бурятия: the place differs, the sentence and the articles do not."""
    born = date(1973, 3, 3)
    with session_factory.begin() as session:
        session.add_all(
            [
                _person(
                    "Ханова Мария Андреевна",
                    born,
                    region="Республика Бурятия",
                    city=None,
                    articles="ст. 282.2 УК РФ ч. 1,ст. 205.5 УК РФ ч. 2",
                    sentenced_on=date(2026, 5, 6),
                    court="2-й Восточный окружной военный суд",
                ),
                # Not her: sentenced too long ago, under another article, never sentenced.
                _person(
                    "Давняя Анна Ильинична",
                    born,
                    region="Тува",
                    city=None,
                    articles="ст. 205.5 УК РФ",
                    sentenced_on=date(2026, 3, 1),
                ),
                _person(
                    "Другая Анна Ильинична",
                    born,
                    region="Тува",
                    city=None,
                    articles="ст. 275 УК РФ",
                    sentenced_on=date(2026, 6, 1),
                ),
                _person(
                    "Несудимая Анна Ильинична",
                    born,
                    region="Тува",
                    city=None,
                    articles="ст. 205.5 УК РФ",
                ),
                # From the place the news names: first, as before.
                _person(
                    "Якутова Анна Ильинична",
                    born,
                    region="Якутия",
                    city="Якутск",
                    articles="ст. 275 УК РФ",
                ),
                # From the place and sentenced under the article that season: found by
                # the place, like anyone of the place.
                _person(
                    "Местная Анна Ильинична",
                    born,
                    region="Якутия",
                    city=None,
                    articles="ст. 205.5 УК РФ",
                    sentenced_on=date(2026, 6, 1),
                ),
            ]
        )


def test_news_of_a_sentence_finds_who_was_sentenced_under_the_article_wherever_from(
    session_factory: sessionmaker[Session],
) -> None:
    _sentenced(session_factory)
    news = _figurant(age=53, place="Якутия", articles=["205.5", "282.2"], event_type="sentence")

    with session_factory() as session:
        found = base_candidates(session, news)
        arrest = base_candidates(session, _figurant(**{**vars(news), "event_type": "arrest"}))
        no_article = base_candidates(session, _figurant(**{**vars(news), "articles": []}))

    # The one from the place first; then the one found by her sentence, who says so.
    assert [(item.name, item.by_sentence) for item in found.shown] == [
        ("Местная Анна Ильинична", False),
        ("Якутова Анна Ильинична", False),
        ("Ханова Мария Андреевна", True),
    ]
    assert found.shown[0].reasons == [
        "53 лет на 11.06.2026",
        "место: Якутия",
        "та же статья: 205.5",
        "в базе уже есть приговор от 01.06.2026",
    ]
    assert found.shown[2].reasons == [
        "53 лет на 11.06.2026",
        "та же статья: 205.5, 282.2",
        "приговор в базе от 06.05.2026, 2-й Восточный окружной военный суд",
        "место в базе другое: Республика Бурятия",
    ]
    assert found.total == 6
    # News of an arrest is no news of a sentence; and with no article named, the people
    # sentenced that season are too many to tell anything.
    assert [item.name.split()[0] for item in arrest.shown] == ["Местная", "Якутова"]
    assert sorted(item.name.split()[0] for item in no_article.shown) == ["Местная", "Якутова"]


def test_people_of_the_place_do_not_hide_the_one_found_by_the_sentence(
    session_factory: sessionmaker[Session],
) -> None:
    """Five seats, six people of the place: the one found by the sentence and the article
    — the stronger sign for news of a sentence — takes the last seat."""
    _sentenced(session_factory)
    with session_factory.begin() as session:
        session.add_all(
            _person(f"Якутянка{n} Анна Ильинична", date(1973, 3, 3), region="Якутия", city=None)
            for n in range(5)
        )
    news = _figurant(age=53, place="Якутия", articles=["205.5", "282.2"], event_type="sentence")

    with session_factory() as session:
        found = base_candidates(session, news)
        arrest = base_candidates(session, _figurant(**{**vars(news), "event_type": "arrest"}))

    assert len(found.shown) == 5 and found.shown[-1].name == "Ханова Мария Андреевна"
    assert not any(item.by_sentence for item in found.shown[:-1])
    # Nobody found by a sentence: the five seats are the place's, as before.
    assert len(arrest.shown) == 5 and not any(item.by_sentence for item in arrest.shown)


def test_the_sentence_is_looked_for_ninety_days_back_and_two_ahead(
    session_factory: sessionmaker[Session],
) -> None:
    born = date(1973, 3, 3)
    days = {"ровно": 90, "раньше": 91, "позже": -2, "ещёпозже": -3}
    with session_factory.begin() as session:
        for name, before in days.items():
            session.add(
                _person(
                    f"{name.capitalize()} Анна Ильинична",
                    born,
                    region="Тува",
                    city=None,
                    articles="ст. 205.5 УК РФ",
                    sentenced_on=NEWS.date() - timedelta(days=before),
                )
            )

    with session_factory() as session:
        found = base_candidates(
            session, _figurant(age=53, place="Якутия", articles=["205.5"], event_type="sentence")
        )

    assert sorted(item.name.split()[0] for item in found.shown) == ["Позже", "Ровно"]


def test_article_numbers_are_read_from_the_base_s_cell() -> None:
    assert article_numbers("Ст. 280 УК РФ") == {"280"}
    assert article_numbers("ст. 205.1 УК РФ ч. 1,ст. 30 УК РФ ч. 3, ст.275 УК РФ") == {
        "205.1",
        "30",
        "275",
    }
    assert article_numbers("Нет информации") == set()
