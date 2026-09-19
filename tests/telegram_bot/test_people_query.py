"""Parsing `/people`: inclusive dates, the timezone conversion and the optional limit."""

from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest

from telegram_bot.people_service import PeopleQuery, PeopleQueryError, parse_people_query

MOSCOW = ZoneInfo("Europe/Moscow")


def parse(*arguments: str, timezone: ZoneInfo = MOSCOW, max_limit: int = 200) -> PeopleQuery:
    return parse_people_query(arguments, timezone=timezone, default_limit=50, max_limit=max_limit)


def test_a_valid_period_uses_the_default_limit() -> None:
    query = parse("2026-09-01", "2026-09-19")

    assert (query.date_from, query.date_to) == (date(2026, 9, 1), date(2026, 9, 19))
    assert query.limit == 50


def test_both_dates_are_included_as_a_half_open_utc_period() -> None:
    query = parse("2026-09-01", "2026-09-19")

    # Moscow is UTC+3: the period opens at 21:00 of the previous day in UTC.
    assert query.start == datetime(2026, 8, 31, 21, tzinfo=UTC)
    assert query.end == datetime(2026, 9, 19, 21, tzinfo=UTC)


def test_another_timezone_shifts_the_boundaries() -> None:
    query = parse("2026-09-01", "2026-09-01", timezone=ZoneInfo("UTC"))

    assert query.start == datetime(2026, 9, 1, tzinfo=UTC)
    assert query.end == datetime(2026, 9, 2, tzinfo=UTC)


def test_one_day_period_is_allowed() -> None:
    query = parse("2026-09-05", "2026-09-05")

    assert query.end - query.start == datetime(2026, 9, 2, tzinfo=UTC) - datetime(
        2026, 9, 1, tzinfo=UTC
    )


def test_a_leap_day_is_a_valid_date() -> None:
    query = parse("2028-02-29", "2028-03-01")

    assert query.date_from == date(2028, 2, 29)


def test_a_non_leap_february_29_is_rejected() -> None:
    with pytest.raises(PeopleQueryError):
        parse("2026-02-29", "2026-03-01")


def test_an_optional_limit_is_used() -> None:
    assert parse("2026-09-01", "2026-09-19", "100").limit == 100


@pytest.mark.parametrize("arguments", [(), ("2026-09-01",), ("a", "b", "c", "d")])
def test_a_wrong_number_of_arguments_is_rejected(arguments: tuple[str, ...]) -> None:
    with pytest.raises(PeopleQueryError):
        parse(*arguments)


@pytest.mark.parametrize("text", ["01.09.2026", "2026-99-99", "2026-9-1x", "вчера"])
def test_a_wrong_date_format_is_rejected(text: str) -> None:
    with pytest.raises(PeopleQueryError) as error:
        parse(text, "2026-09-19")
    assert "YYYY-MM-DD" in str(error.value)


def test_a_reversed_period_is_rejected() -> None:
    with pytest.raises(PeopleQueryError) as error:
        parse("2026-09-20", "2026-09-01")
    assert "позже" in str(error.value)


@pytest.mark.parametrize("text", ["abc", "0", "-5", "201"])
def test_an_invalid_limit_is_rejected(text: str) -> None:
    with pytest.raises(PeopleQueryError):
        parse("2026-09-01", "2026-09-19", text)


def test_the_maximum_limit_is_accepted() -> None:
    assert parse("2026-09-01", "2026-09-19", "200").limit == 200
