"""What the operator's own base says of a person found here — by name, and how sure."""

from __future__ import annotations

from sqlalchemy.orm import Session, sessionmaker

from db.orm_models import AirtableKnownPersonRecord
from entities.known_base import KnownBase, TrackedCase, TrackedCases


def _answer(base: KnownBase, name: str) -> tuple[str, int] | None:
    match = base.match(name)
    return (match.level, match.count) if match else None


def test_the_same_words_in_any_order_and_case_are_the_base_s_person() -> None:
    base = KnownBase(["Смирнова Анна Петровна", "Попов Иван"])

    # The pipeline writes the given name first, the base the surname first; «ё» is «е».
    assert _answer(base, "Анна Петровна Смирнова") == ("in_base", 1)
    assert _answer(base, "иван ПОПОВ") == ("in_base", 1)
    assert _answer(KnownBase(["Фёдоров Артём"]), "Артем Федоров") == ("in_base", 1)


def test_a_name_the_base_holds_more_or_less_of_is_probably_the_person() -> None:
    base = KnownBase(["Попов Андрей Иванович", "Ольман Семён"])

    # No patronymic on our side, or none in the base: one record, another spelling.
    probably = base.match("Андрей Попов")
    assert probably is not None and probably.level == "probably"
    assert probably.names == ("Попов Андрей Иванович",)
    assert _answer(base, "Семён Андреевич Ольман") == ("probably", 1)


def test_several_records_that_fit_are_namesakes_and_none_is_picked() -> None:
    base = KnownBase(
        [
            "Дмитриев Юрий Алексеевич",
            "Дмитриев Юрий Сергеевич (Дмитрієв Юрій Сергійович)",
            "Дмитриев Юрий Павлович",
            "Дмитриев Юрий Олегович",
            "Петров Пётр",
        ]
    )

    match = base.match("Юрий Дмитриев")

    assert match is not None and (match.level, match.count) == ("namesakes", 4)
    # Only a few are named; the label says how many.
    assert len(match.names) == 3 and match.label == "тёзки в базе: 4"


def test_a_name_with_a_patronymic_that_one_record_has_exactly_is_the_person() -> None:
    base = KnownBase(["Иванов Иван", "Иванов Иван Петрович", "Иванов Иван Сергеевич"])

    # Three words, exactly one record with all of them: the others are shorter or other.
    assert _answer(base, "Иван Петрович Иванов") == ("in_base", 1)
    # Two words: three records fit and none can be told from the name alone.
    assert _answer(base, "Иван Иванов") == ("namesakes", 3)


def test_the_two_spellings_of_one_record_are_one_record() -> None:
    base = KnownBase(["Лагода Роман Александрович (Лагода Роман Олександрович)"])

    assert _answer(base, "Роман Лагода") == ("probably", 1)
    # The second spelling, in full: the record's own name.
    assert _answer(base, "Роман Олександрович Лагода") == ("in_base", 1)
    # Letters the Russian alphabet lacks do not break a word apart.
    ukrainian = KnownBase(["Дмитриев Юрий (Дмитрієв Юрій)", "Gershkovich Evan"])
    assert _answer(ukrainian, "Юрій Дмитрієв") == ("in_base", 1)
    assert _answer(ukrainian, "Evan Gershkovich") == ("in_base", 1)


def test_nobody_by_this_name_is_not_in_the_base() -> None:
    base = KnownBase(["Попов Иван", "Иванов Пётр"])

    assert base.match("Олег Новиков") is None
    # One shared word is no match: «Иван» names half the city.
    assert base.match("Иван Сидоров") is None
    assert base.match("Иван") is None


def test_records_that_are_no_person_are_left_out() -> None:
    base = KnownBase(
        ["Житель Курской области 1", "Неизвестный житель Екатеринбурга 2", "Одно", "  "]
    )

    assert len(base) == 0
    assert base.match("Житель Курской") is None


def test_only_the_active_records_of_the_base_count(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as session:
        for external_id, name, active in (
            ("a", "Смирнова Анна", True),
            ("b", "Петров Пётр", False),
        ):
            session.add(
                AirtableKnownPersonRecord(
                    external_id=external_id,
                    full_name=name,
                    normalized_name=name.lower(),
                    matching_key=name.lower().replace(" ", ""),
                    active=active,
                )
            )

    with session_factory() as session:
        base = KnownBase.from_session(session)

    assert len(base) == 1
    assert base.match("Анна Смирнова") is not None and base.match("Пётр Петров") is None


def test_a_case_is_tracked_by_the_one_record_of_the_name_and_a_shared_article() -> None:
    cases = TrackedCases(
        [
            ("Котов Пётр Ильич", "ст. 228.1 УК РФ ч. 4 п. г,ст. 30 УК РФ ч. 3"),
            ("Быков Алексей Викторович", "ст. 205.2 УК РФ"),
            ("Орлов Иван Олегович", "ст. 228.1 УК РФ"),
            ("Орлов Иван Петрович", "ст. 228.1 УК РФ"),
            ("Лугин Анна", None),
        ]
    )

    # The news leaves the patronymic out: the same case, and said to be so by a part of
    # the name; with the patronymic, by the whole of it.
    assert cases.match("Пётр Котов", ["228.1"]) == TrackedCase(
        "Котов Пётр Ильич", ("228.1",), full_name=False
    )
    assert cases.match("Пётр Ильич Котов", ["228.1"]) == TrackedCase(
        "Котов Пётр Ильич", ("228.1",), full_name=True
    )
    # A namesake: the record is of another crime.
    assert cases.match("Алексей Быков", ["159"]) is None
    # Two records by the name: which one, a shared article does not tell.
    assert cases.match("Иван Орлов", ["228.1"]) is None
    # An attempt is no article of a case: «ч. 4» and «ст. 30» name nothing.
    assert cases.match("Пётр Котов", ["30"]) is None and cases.match("Пётр Котов", ["4"]) is None
    assert cases.match("Анна Лугин", ["110.2"]) is None
    assert cases.match("Никто Такой", ["228.1"]) is None


def test_only_today_s_criminal_code_makes_a_case_the_same() -> None:
    cases = TrackedCases(
        [
            ("Старов Иван Ильич", "ст. 64   УК РСФСР ч. 2 п. а"),
            ("Штрафов Иван Ильич", "ст. 20.3.3 КоАП РФ"),
            ("Двойнов Иван Ильич", "ст.ст. 205, 208 УК РФ"),
            ("Словов Иван Ильич", "статья 205 УК РФ"),
            ("Верный Иван Ильич", "ст. 226.1 УК РФ ч. 1,ст. 283 УК РФ ч. 1"),
        ]
    )

    # Another code's number is another crime; a notation not known is not guessed at.
    assert cases.match("Иван Старов", ["64"]) is None
    assert (
        cases.match("Иван Штрафов", ["20.3"]) is None
        and cases.match("Иван Штрафов", ["20"]) is None
    )
    assert cases.match("Иван Двойнов", ["205"]) is None
    assert cases.match("Иван Словов", ["205"]) is None
    assert cases.match("Иван Верный", ["283"]) == TrackedCase(
        "Верный Иван Ильич", ("283",), full_name=False
    )
