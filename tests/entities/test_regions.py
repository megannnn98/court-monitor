"""One name for each subject of the Federation, however it is written."""

from __future__ import annotations

import pytest

from entities.regions import REGIONS, canonical


def test_the_list_is_the_federations() -> None:
    assert len(REGIONS) == len(set(REGIONS)) == 89
    assert all(canonical(region) == region for region in REGIONS)


@pytest.mark.parametrize(
    ("written", "region"),
    [
        ("Чечня", "Чеченская Республика"),
        ("крым", "Республика Крым"),
        ("г. Москва", "Москва"),
        ("Санкт‑Петербург", "Санкт-Петербург"),
        ("Луганская область", "Луганская Народная Республика"),
        ("Кемеровская область — Кузбасс", "Кемеровская область"),
        ("ХМАО", "Ханты-Мансийский автономный округ — Югра"),
        ("Свердловская  область", "Свердловская область"),
    ],
)
def test_a_spelling_is_its_subject(written: str, region: str) -> None:
    assert canonical(written) == region


@pytest.mark.parametrize("written", ["", "Донецк", "Алтай", "Россия", "Тбилиси"])
def test_what_is_no_one_subject_is_none(written: str) -> None:
    # «Алтай» is two subjects, a city is not a subject: neither is guessed.
    assert canonical(written) == ""
