"""Who on the list a nameless record of the operator's base may be."""

from __future__ import annotations

from datetime import UTC, date, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from db.orm_models import (
    AirtableKnownPersonRecord,
    RosfinmonitoringEntryRecord,
    RosfinmonitoringSnapshotRecord,
)
from entities.base_unnamed import (
    RfEntry,
    RfList,
    age_on,
    candidates_for,
    counts,
    entry_gender,
    nameless_cases,
    nameless_records,
    record_age,
    say,
)
from entities.unnamed import DIFFERENT, SAME

OPENED = date(2025, 6, 9)


def _entry(
    name: str, born: date, place: str, included: date | None = date(2025, 10, 14)
) -> RfEntry:
    return RfEntry(name, name.lower(), born, place, included)


def _record(**values: Any) -> SimpleNamespace:
    return SimpleNamespace(
        **{
            "external_id": "rec1",
            "full_name": "50-летний житель Рубцовска",
            "gender": "male",
            "region": "Алтайский край",
            "city": "Рубцовск",
            "case_opened_on": OPENED,
            **values,
        }
    )


BUDNIKOV = _entry("БУДНИКОВ ЕВГЕНИЙ АНАТОЛЬЕВИЧ*", date(1975, 10, 9), "Г. РУБЦОВСК АЛТАЙСКОГО КРАЯ")
ALTAI = _entry(
    "АЛТАЕВ ПЁТР ИЛЬИЧ", date(1975, 1, 1), "С. ПАВЛОВСК АЛТАЙСКОГО КРАЯ", date(2026, 1, 1)
)
# On the list a day before the case was opened: the base may date the case late.
EARLY = _entry("РАННИЙ ИВАН ИЛЬИЧ", date(1975, 5, 5), "Г. РУБЦОВСК", date(2025, 6, 8))
LIST = RfList(
    [
        ALTAI,
        BUDNIKOV,
        # Not him: a woman, born elsewhere, too young.
        _entry("РУБЦОВА АННА ИЛЬИНИЧНА", date(1975, 5, 5), "Г. РУБЦОВСК АЛТАЙСКОГО КРАЯ"),
        _entry("ОМСКИЙ ИВАН ИЛЬИЧ", date(1975, 5, 5), "Г. ОМСК"),
        _entry("МОЛОДОЙ ИВАН ИЛЬИЧ", date(1980, 5, 5), "Г. РУБЦОВСК АЛТАЙСКОГО КРАЯ"),
        EARLY,
    ]
)


def test_the_entries_of_that_age_sex_and_birth_place_included_after_the_case() -> None:
    case = candidates_for(_record(), LIST, {})

    assert case is not None and case.age == 50 and case.total == 3
    # Born in the very city first, then in the region; the one on the list before the
    # case below them all, though born in the city.
    assert [item.entry.full_name for item in case.candidates] == [
        "БУДНИКОВ ЕВГЕНИЙ АНАТОЛЬЕВИЧ*",
        "АЛТАЕВ ПЁТР ИЛЬИЧ",
        "РАННИЙ ИВАН ИЛЬИЧ",
    ]
    assert case.candidates[2].reasons[-1] == "включён в перечень 08.06.2025, до возбуждения дела"
    first = case.candidates[0]
    assert first.entry.key == "будников евгений анатольевич*|1975-10-09"
    # 49 on the day the case was opened: the record's age is the news's.
    assert first.reasons == [
        "49 лет на 09.06.2025",
        "родился: Г. РУБЦОВСК АЛТАЙСКОГО КРАЯ",
        "включён в перечень 14.10.2025, после возбуждения дела",
    ]
    assert case.newest == date(2026, 1, 1)


def test_the_age_may_be_a_year_off_either_way_and_no_more() -> None:
    def names(age: int) -> list[str]:
        case = candidates_for(_record(full_name=f"{age}-летний житель Рубцовска"), LIST, {})
        assert case is not None
        return [item.entry.full_name for item in case.candidates]

    # Будников is 49 on the day, Алтаев 50.
    assert names(48) == ["БУДНИКОВ ЕВГЕНИЙ АНАТОЛЬЕВИЧ*"]
    assert names(49) == ["БУДНИКОВ ЕВГЕНИЙ АНАТОЛЬЕВИЧ*", "АЛТАЕВ ПЁТР ИЛЬИЧ", "РАННИЙ ИВАН ИЛЬИЧ"]
    assert names(51) == ["АЛТАЕВ ПЁТР ИЛЬИЧ", "РАННИЙ ИВАН ИЛЬИЧ"]
    assert names(52) == []


def test_what_the_record_does_not_say_does_not_narrow() -> None:
    woman = candidates_for(_record(gender="female"), LIST, {})
    anyone = candidates_for(_record(gender=None, city=None), LIST, {})
    undated = candidates_for(
        _record(), RfList([_entry("ТИХИЙ ИВАН ИЛЬИЧ", date(1975, 5, 5), "Г. РУБЦОВСК", None)]), {}
    )

    assert woman is not None and [item.entry.full_name for item in woman.candidates] == [
        "РУБЦОВА АННА ИЛЬИНИЧНА"
    ]
    # No sex and no city: everyone of the region, nobody above another by the city.
    assert anyone is not None and anyone.total == 3
    assert not any(item.city_match for item in anyone.candidates)
    assert undated is not None and undated.candidates[0].reasons[-1] == (
        "дата включения в перечень неизвестна"
    )
    assert undated.newest == date.min
    assert candidates_for(_record(full_name="Житель Рубцовска 3"), LIST, {}) is None
    assert candidates_for(_record(case_opened_on=None), LIST, {}) is None


def test_a_person_s_word_orders_and_the_rest_is_cut() -> None:
    words = {BUDNIKOV.key: DIFFERENT}
    case = candidates_for(_record(), LIST, words, shown=1)
    same = candidates_for(_record(), LIST, {ALTAI.key: SAME})

    assert case is not None and case.total == 3 and case.confirmed is None
    assert [item.entry.full_name for item in case.candidates] == ["АЛТАЕВ ПЁТР ИЛЬИЧ"]
    assert same is not None and same.identified is not None and same.confirmed == ALTAI.key
    assert same.identified.entry is ALTAI and same.candidates[0].entry is ALTAI
    assert [item.entry for item in same.open] == [BUDNIKOV, EARLY]
    # The freshest of those still to look at, not of the one already confirmed.
    assert same.newest == date(2025, 10, 14)
    # Only the one on the list before the case is left to look at: nothing fresh.
    stale = candidates_for(_record(), LIST, {ALTAI.key: DIFFERENT, BUDNIKOV.key: DIFFERENT})
    assert stale is not None and [item.entry for item in stale.open] == [EARLY]
    assert stale.newest == date.min


def test_a_word_on_an_entry_that_left_the_list_stays() -> None:
    case = candidates_for(_record(), LIST, {"ушедший иван ильич|1975-03-03": SAME})

    assert case is not None and case.identified is None
    assert case.confirmed == "ушедший иван ильич|1975-03-03"
    assert counts([case]) == {"open": 0, "found": 1, "all": 1}


def test_a_birth_place_is_found_where_a_word_begins() -> None:
    tomsk = _entry("ТОМСКИЙ ИВАН ИЛЬИЧ", date(1975, 5, 5), "Г. ТОМСК")
    omsk = _entry("ОМСКИЙ ИВАН ИЛЬИЧ", date(1975, 5, 5), "Г. ОМСК ОМСКОЙ ОБЛАСТИ")
    ufa = _entry("УФИМСКИЙ ИВАН ИЛЬИЧ", date(1975, 5, 5), "Г. УФА")

    def names(**place: str | None) -> list[str]:
        case = candidates_for(_record(**place), RfList([tomsk, omsk, ufa]), {})
        assert case is not None
        return [item.entry.full_name for item in case.candidates]

    assert names(region="Омская область", city="Омск") == ["ОМСКИЙ ИВАН ИЛЬИЧ"]
    assert names(region="Башкортостан", city="Уфа") == ["УФИМСКИЙ ИВАН ИЛЬИЧ"]
    assert names(region="", city=None) == []


def test_the_newer_on_the_list_stands_above_among_equals() -> None:
    old = _entry("СТАРЫЙ ИВАН ИЛЬИЧ", date(1975, 2, 2), "Г. РУБЦОВСК", date(2025, 7, 1))
    new = _entry("ЯНОВ ИВАН ИЛЬИЧ", date(1975, 3, 3), "Г. РУБЦОВСК", date(2026, 9, 1))
    none = _entry("АБАЕВ ИВАН ИЛЬИЧ", date(1975, 4, 4), "Г. РУБЦОВСК", None)

    # A year off, though the latest of all: after everyone of the exact age.
    off = _entry("ЮНЫЙ ИВАН ИЛЬИЧ", date(1975, 10, 9), "Г. РУБЦОВСК", date(2026, 12, 1))
    # Included on the very day the case was opened: not before it.
    that_day = _entry("ДНЁВ ИВАН ИЛЬИЧ", date(1975, 5, 5), "Г. РУБЦОВСК", OPENED)

    # On the list before the case, though born in the city and of the exact age: last.
    before = _entry("АРХИПОВ ИВАН ИЛЬИЧ", date(1975, 5, 5), "Г. РУБЦОВСК", date(2025, 6, 8))

    case = candidates_for(_record(), RfList([before, off, old, none, that_day, new]), {}, shown=9)

    assert case is not None
    assert [item.entry for item in case.candidates] == [new, old, that_day, none, off, before]


def test_names_ages_and_sexes_are_read() -> None:
    assert record_age("34-летний уроженец Крыма") == 34 and record_age("17 летняя") == 17
    assert record_age("Житель Курской области 3") is None
    assert age_on(date(2025, 10, 8), date(1975, 10, 9)) == 49
    assert age_on(date(2025, 10, 9), date(1975, 10, 9)) == 50
    assert entry_gender("БУДНИКОВ ЕВГЕНИЙ АНАТОЛЬЕВИЧ*") == "male"
    assert entry_gender("ИВАНОВА МАРИЯ ИЛЬИНИЧНА") == "female"
    assert entry_gender("АЛИЕВ АЛИ МАМЕД ОГЛЫ") == "male"
    assert entry_gender("СМИТ ДЖОН") is None


def _seed(session_factory: sessionmaker[Session]) -> None:
    with session_factory.begin() as session:
        for index, (name, born, opened) in enumerate(
            [
                ("50-летний житель Рубцовска", None, OPENED),
                ("34-летний уроженец Омска", None, date(2026, 1, 10)),
                # Not to identify: named, born, without the day the case was opened, ageless.
                ("Иванов Иван Иванович", None, OPENED),
                ("50-летний житель Рубцовска (2)", date(1975, 1, 1), OPENED),
                ("50-летний житель Барнаула", None, None),
                ("Житель Рубцовска 3", None, OPENED),
                # To identify, but nobody on the list is that age.
                ("20-летний житель Рубцовска", None, OPENED),
            ]
        ):
            session.add(
                AirtableKnownPersonRecord(
                    external_id=f"rec{index}",
                    full_name=name,
                    normalized_name=name.lower(),
                    matching_key=name.lower(),
                    gender="male",
                    region="Омская область" if "Омска" in name else "Алтайский край",
                    city="Омск" if "Омска" in name else "Рубцовск",
                    birth_date=born,
                    case_opened_on=opened,
                )
            )
        for day, entries in (
            (1, [("СТАРЫЙ СНИМОК ИЛЬИЧ", date(1975, 10, 9), "Г. РУБЦОВСК", date(2025, 10, 14))]),
            (
                2,
                [
                    (
                        "БУДНИКОВ ЕВГЕНИЙ АНАТОЛЬЕВИЧ*",
                        date(1975, 10, 9),
                        "Г. РУБЦОВСК",
                        date(2025, 10, 14),
                    ),
                    ("ОМСКИЙ ИВАН ИЛЬИЧ", date(1991, 5, 5), "Г. ОМСК", date(2026, 9, 30)),
                    ("БЕЗДАТНЫЙ ИВАН ИЛЬИЧ", None, "Г. РУБЦОВСК", date(2025, 10, 14)),
                ],
            ),
        ):
            snapshot = RosfinmonitoringSnapshotRecord(
                snapshot_date=datetime(2026, 9, day, tzinfo=UTC),
                source_url="https://fedsfm.test",
                content_hash=str(day),
                entry_count=len(entries),
                fetched_at=datetime(2026, 9, day, tzinfo=UTC),
            )
            session.add(snapshot)
            session.flush()
            for name, born, place, included in entries:
                session.add(
                    RosfinmonitoringEntryRecord(
                        snapshot_id=snapshot.id,
                        full_name=name,
                        normalized_name=name.lower(),
                        matching_key=name.lower().replace(" ", ""),
                        birth_date=datetime(born.year, born.month, born.day, tzinfo=UTC)
                        if born
                        else None,
                        birth_place=place,
                        inclusion_date=datetime(
                            included.year, included.month, included.day, tzinfo=UTC
                        ),
                    )
                )


def test_the_nameless_records_of_the_base_against_the_latest_snapshot(
    session_factory: sessionmaker[Session],
) -> None:
    _seed(session_factory)

    with session_factory() as session:
        records = nameless_records(session)
        cases = nameless_cases(session)

    assert sorted(row.full_name for row in records) == [
        "20-летний житель Рубцовска",
        "34-летний уроженец Омска",
        "50-летний житель Рубцовска",
    ]
    # The one whose candidate came onto the list last stands first.
    assert [
        (case.record.full_name, [item.entry.full_name for item in case.candidates])
        for case in cases
    ] == [
        ("34-летний уроженец Омска", ["ОМСКИЙ ИВАН ИЛЬИЧ"]),
        ("50-летний житель Рубцовска", ["БУДНИКОВ ЕВГЕНИЙ АНАТОЛЬЕВИЧ*"]),
    ]
    assert counts(cases) == {"open": 2, "found": 0, "all": 2}


def test_a_confirmed_record_stays_though_no_entry_fits_now(
    session_factory: sessionmaker[Session],
) -> None:
    _seed(session_factory)

    with session_factory.begin() as session:
        # «20-летний житель Рубцовска»: nobody on the list is that age.
        say(session, "rec6", "ушедший иван ильич|2005-03-03", SAME)
    with session_factory() as session:
        cases = nameless_cases(session)

    assert counts(cases) == {"open": 2, "found": 1, "all": 3}
    kept = next(case for case in cases if case.record.external_id == "rec6")
    assert (kept.candidates, kept.confirmed) == ([], "ушедший иван ильич|2005-03-03")


def test_a_word_stays_on_a_record_that_can_no_longer_be_compared(
    session_factory: sessionmaker[Session],
) -> None:
    _seed(session_factory)

    with session_factory.begin() as session:
        # No age in the name; no day the case was opened.
        say(session, "rec5", "будников|1975-10-09", SAME)
        say(session, "rec4", "барнаулов|1975-10-09", SAME)
        # Only rejected: no word to keep in sight.
        say(session, "rec2", "кто-то|1975-10-09", DIFFERENT)
        # Got a birth date: a named person now, nobody to identify.
        say(session, "rec3", "рубцов|1975-01-01", SAME)
    with session_factory() as session:
        cases = nameless_cases(session)

    assert [(case.record.full_name, case.age, case.confirmed) for case in cases] == [
        ("34-летний уроженец Омска", 34, None),
        ("50-летний житель Рубцовска", 50, None),
        # Nothing fresh to look at: after the others, by name.
        ("50-летний житель Барнаула", 50, "барнаулов|1975-10-09"),
        ("Житель Рубцовска 3", None, "будников|1975-10-09"),
    ]
    assert counts(cases) == {"open": 2, "found": 2, "all": 4}


def test_a_word_is_kept_changed_and_taken_back(session_factory: sessionmaker[Session]) -> None:
    _seed(session_factory)
    budnikov = "будников евгений анатольевич*|1975-10-09"

    def words() -> list[tuple[str, str, str]]:
        with session_factory() as session:
            return [
                tuple(row)
                for row in session.execute(
                    text(
                        "SELECT figurant_key, candidate, decision FROM unnamed_decisions "
                        "ORDER BY figurant_key, candidate"
                    )
                )
            ]

    with session_factory.begin() as session:
        say(session, "rec0", "кто-то|1975-01-01", SAME)
        say(session, "rec0", "другой|1975-01-01", DIFFERENT)
        say(session, "rec1", "омский иван ильич|1991-05-05", SAME)
        # «Это он» on another entry: one entry is the record at most.
        say(session, "rec0", budnikov, SAME)
    assert words() == [
        ("rec0", budnikov, SAME),
        ("rec0", "другой|1975-01-01", DIFFERENT),
        ("rec1", "омский иван ильич|1991-05-05", SAME),
    ]
    with session_factory() as session:
        cases = nameless_cases(session)
    assert counts(cases) == {"open": 0, "found": 2, "all": 2}
    assert [item.decision for item in cases[-1].candidates] == [SAME]

    with session_factory.begin() as session:
        say(session, "rec0", budnikov, DIFFERENT)
    with session_factory() as session:
        rejected = nameless_cases(session)
    # Every candidate rejected: neither waiting nor identified.
    assert counts(rejected) == {"open": 0, "found": 1, "all": 2}

    with session_factory.begin() as session:
        say(session, "rec0", budnikov, None)
        with pytest.raises(ValueError):
            say(session, "rec0", budnikov, "maybe")
    assert words() == [
        ("rec0", "другой|1975-01-01", DIFFERENT),
        ("rec1", "омский иван ильич|1991-05-05", SAME),
    ]


def test_a_row_removed_from_the_list_is_compared_with_a_nameless_record(
    session_factory: sessionmaker[Session],
) -> None:
    """«20-летний житель Рубцовска»: nobody on the list is that age — but the operator's
    table holds one who was on it and has been removed since."""
    from rosfinmonitoring.operator_table import OperatorRow, store

    _seed(session_factory)
    with session_factory.begin() as session:
        store(
            session,
            [
                OperatorRow(
                    "УШЕДШИЙ ИВАН ИЛЬИЧ",
                    date(2005, 3, 3),
                    "Г. РУБЦОВСК АЛТАЙСКОГО КРАЯ",
                    date(2025, 7, 1),
                    True,
                    "экстремизм",
                    "",
                )
            ],
        )
    with session_factory() as session:
        cases = nameless_cases(session)

    case = next(case for case in cases if case.record.external_id == "rec6")
    [gone] = case.candidates
    assert gone.entry.full_name == "УШЕДШИЙ ИВАН ИЛЬИЧ" and gone.entry.removed
    assert gone.reasons[-2:] == [
        "включён в перечень 01.07.2025, после возбуждения дела",
        "позже исключён из перечня — по таблице оператора",
    ]
