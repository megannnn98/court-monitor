"""Common-name preferences must not silently choose between conflicting entries."""

import pytest

from extraction.name_frequency import FEMALE_NAMES, MALE_NAMES, lookup_gender


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("АРТЁМ", "masc"),
        ("Артем", "masc"),
        ("Лидия", "femn"),
        ("Александр", "masc"),
        ("Александра", "femn"),
        ("Фома", None),  # Present in both inherited lists: do not silently prefer male.
        ("несуществующееимя", None),
        ("Мaрия", None),  # Latin a must not be treated as a Russian given name.
    ],
)
def test_common_name_lookup(name: str, expected: str | None) -> None:
    assert lookup_gender(name) == expected


def test_given_name_lists_contain_only_russian_letters() -> None:
    assert all(
        all("а" <= letter <= "я" or letter == "ё" for letter in name)
        for name in MALE_NAMES | FEMALE_NAMES
    )
