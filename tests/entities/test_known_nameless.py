"""An unnamed person of the news against the base's records without a name."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from typing import Any

from entities.known_base import KnownMatch
from entities.known_nameless import NamelessBase

DAY = date(2026, 10, 1)


def _record(name: str, **values: Any) -> SimpleNamespace:
    return SimpleNamespace(
        **{
            "full_name": name,
            "gender": "male",
            "region": "Ленинградская область",
            "city": None,
            "case_opened_on": date(2026, 9, 30),
            **values,
        }
    )


def test_one_record_that_tells_the_same_is_probably_the_person() -> None:
    base = NamelessBase(
        [
            _record("41-летний  житель Ленинградской области"),
            _record("41-летняя жительница Ленинградской области", gender="female"),
            _record("41-летний житель Тулы", region="Тульская область", city="Тула"),
            _record("Житель Ленинградской области 3"),
        ]
    )

    assert base.match(41, "male", "Ленинградская область", DAY) == KnownMatch(
        "probably", ("41-летний житель Ленинградской области",), 1
    )
    # The news does not say the sex: both records fit, and neither is picked.
    assert base.match(41, "", "Ленинградская область", DAY) == KnownMatch(
        "similar",
        (
            "41-летний житель Ленинградской области",
            "41-летняя жительница Ленинградской области",
        ),
        2,
    )
    assert base.match(41, "male", "Ленинградская область", DAY).label == "вероятно, есть в базе"  # type: ignore[union-attr]
    assert base.match(41, "", "Ленинградская область", DAY).label == "похожие записи в базе: 2"  # type: ignore[union-attr]


def test_nothing_to_compare_is_no_answer() -> None:
    base = NamelessBase([_record("41-летний житель Ленинградской области")])

    assert base.match(None, "male", "Ленинградская область", DAY) is None
    assert base.match(41, "male", "", DAY) is None
    assert base.match(41, "female", "Ленинградская область", DAY) is None
    assert base.match(41, "male", "Тула", DAY) is None
    assert base.match(30, "male", "Ленинградская область", DAY) is None
    # The base does not say the sex of its record: not a reason to miss it.
    unsexed = NamelessBase([_record("41-летний житель Ленинградской области", gender=None)])
    assert unsexed.match(41, "female", "Ленинградская область", DAY) is not None


def test_the_record_s_age_is_moved_to_the_day_of_the_news() -> None:
    """A record entered two years ago as «39-летний» is 41 today; one entered last week as
    «39-летний» is not."""
    old = NamelessBase(
        [_record("39-летний житель Ленинградской области", case_opened_on=date(2024, 9, 1))]
    )
    fresh = NamelessBase(
        [_record("39-летний житель Ленинградской области", case_opened_on=date(2026, 9, 24))]
    )
    undated = NamelessBase([_record("40-летний житель Ленинградской области", case_opened_on=None)])

    assert old.match(41, "male", "Ленинградская область", DAY) is not None
    assert old.match(39, "male", "Ленинградская область", DAY) is None
    assert fresh.match(41, "male", "Ленинградская область", DAY) is None
    # A year off either way is a birthday or a rough date; with no date, as told.
    assert fresh.match(40, "male", "Ленинградская область", DAY) is not None
    assert undated.match(41, "male", "Ленинградская область", DAY) is not None
    assert undated.match(42, "male", "Ленинградская область", DAY) is None
    assert undated.match(41, "male", "Ленинградская область", None) is not None
