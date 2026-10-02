"""What the sync keeps of a known person beside the name."""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from airtable.client import AirtableRecord
from airtable.person_details import dates, details
from db.orm_models import AirtableKnownPersonRecord

_ROW = {
    "Преследуемый": "Иванова Мария Ивановна",
    "✦пол": "женский",
    "Дата рождения": "January 1, 1999",
    "✦Регион, где возбудили УД/задержали": "Крым",
    "✦Город": "Симферополь",
    "Статья": "ст. 205 УК РФ ч. 1,ст. 222.1 УК РФ ч. 1",
    "Дата возбуждения УД": "March 5, 2024, February 10, 2021",
    "Дата приговора": "June 9, 2022, December 18, 2025",
    "Суд вынесший приговор": "Южный окружной военный суд",
    "ссылка на карточку дела на сайте суда": "https://court.example/case",
    "✦Росфинмониторинг": "1 checked out of 1",
    "✦Дата включения в список РФМ": "October 14, 2025",
}


def test_a_share_link_row_is_read_as_what_its_columns_hold() -> None:
    assert details(AirtableRecord("rec1", _ROW)) == {
        "gender": "female",
        "birth_date": date(1999, 1, 1),
        "region": "Крым",
        "city": "Симферополь",
        "articles": "ст. 205 УК РФ ч. 1,ст. 222.1 УК РФ ч. 1",
        # Tried twice: the case is as old as its first opening, the last sentence stands.
        "case_opened_on": date(2021, 2, 10),
        "sentenced_on": date(2025, 12, 18),
        "court": "Южный окружной военный суд",
        "court_card_url": "https://court.example/case",
        "in_rfm": True,
        "rfm_included_on": date(2025, 10, 14),
    }


def test_a_cell_with_nothing_in_it_is_nothing() -> None:
    read = details(
        AirtableRecord(
            "rec1",
            {
                "✦пол": "нет информации",
                "Статья": "Нет информации",
                "Дата рождения": "",
                "✦Росфинмониторинг": "0 checked out of 1",
            },
        )
    )
    assert read["in_rfm"] is False
    assert {name: value for name, value in read.items() if name != "in_rfm"} == dict.fromkeys(
        (
            "gender",
            "birth_date",
            "region",
            "city",
            "articles",
            "case_opened_on",
            "sentenced_on",
            "court",
            "court_card_url",
            "rfm_included_on",
        )
    )
    assert details(AirtableRecord("rec2", {}))["in_rfm"] is None


def test_two_birth_dates_are_not_a_birth_date() -> None:
    read = details(AirtableRecord("rec1", {"Дата рождения": "May 1, 1990, May 2, 1990"}))
    assert read["birth_date"] is None


def test_dates_are_read_in_the_three_ways_they_are_written() -> None:
    assert dates("December 18, 1998") == [date(1998, 12, 18)]
    assert dates("18.12.1998") == [date(1998, 12, 18)]
    assert dates("1998-12-18") == [date(1998, 12, 18)]
    assert dates("Smarch 18, 1998; February 30, 1998; скоро") == []


def test_the_api_checkbox_is_a_boolean() -> None:
    assert details(AirtableRecord("rec1", {"✦Росфинмониторинг": True}))["in_rfm"] is True


def test_the_sync_keeps_the_details_and_follows_a_change(
    session_factory: sessionmaker[Session],
) -> None:
    from airtable.repository import sync_known_persons

    with session_factory.begin() as session:
        first = sync_known_persons(session, [AirtableRecord("rec1", _ROW)])
    assert first.created == 1
    with session_factory() as session:
        row = session.scalars(select(AirtableKnownPersonRecord)).one()
        assert (row.gender, row.birth_date, row.region, row.in_rfm) == (
            "female",
            date(1999, 1, 1),
            "Крым",
            True,
        )
        assert row.sentenced_on == date(2025, 12, 18)

    with session_factory.begin() as session:
        again = sync_known_persons(session, [AirtableRecord("rec1", _ROW)])
    assert (again.unchanged, again.updated) == (1, 0)

    moved = {**_ROW, "Дата приговора": "June 9, 2022, December 18, 2025, July 1, 2026"}
    with session_factory.begin() as session:
        changed = sync_known_persons(session, [AirtableRecord("rec1", moved)])
    assert changed.updated == 1
    with session_factory() as session:
        row = session.scalars(select(AirtableKnownPersonRecord)).one()
        assert row.sentenced_on == date(2026, 7, 1)
