"""What the operator's base says of a person beside the name.

The base holds, for each person, the sex, the birth date, where the case was opened, the
articles, the sentence and its court, and whether Rosfinmonitoring's list has them. The
sync keeps these beside the name, so that a news item naming nobody («осуждён 27-летний
житель Крыма») can be put next to the people of the base it may be about.

A share link gives every cell as text, as Airtable prints it: a date is «December 18,
1998», a checkbox is «1 checked out of 1», and a cell of several values joins them with
a comma. Nothing here guesses: a cell that is not read as what its column holds is None.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from airtable.client import AirtableRecord

MALE = "male"
FEMALE = "female"

_GENDER_FIELDS = ("gender", "✦пол", "пол", "Пол")
_BIRTH_DATE_FIELDS = ("birth_date", "Birth date", "Дата рождения")
_REGION_FIELDS = ("region", "✦Регион, где возбудили УД/задержали", "Регион")
_CITY_FIELDS = ("city", "✦Город", "Город")
_ARTICLES_FIELDS = ("articles", "Статья", "Статьи")
_CASE_OPENED_FIELDS = ("case_opened_on", "Дата возбуждения УД")
_SENTENCED_FIELDS = ("sentenced_on", "Дата приговора")
_COURT_FIELDS = ("court", "Суд вынесший приговор")
_COURT_CARD_FIELDS = ("court_card_url", "ссылка на карточку дела на сайте суда")
_IN_RFM_FIELDS = ("in_rfm", "✦Росфинмониторинг", "Росфинмониторинг")
_RFM_INCLUDED_FIELDS = ("rfm_included_on", "✦Дата включения в список РФМ")

_GENDERS = {"мужской": MALE, "женский": FEMALE, MALE: MALE, FEMALE: FEMALE}
# What the base writes into a cell it has nothing for.
_NOTHING = {"нет информации"}

_MONTHS = {
    name: number
    for number, name in enumerate(
        (
            "january",
            "february",
            "march",
            "april",
            "may",
            "june",
            "july",
            "august",
            "september",
            "october",
            "november",
            "december",
        ),
        start=1,
    )
}
# «December 18, 1998», «18.12.1998», «1998-12-18».
_DATE = re.compile(
    r"(?P<month_name>[A-Za-z]+) (?P<day_us>\d{1,2}), (?P<year_us>\d{4})"
    r"|(?P<day>\d{1,2})\.(?P<month>\d{1,2})\.(?P<year>\d{4})"
    r"|(?P<year_iso>\d{4})-(?P<month_iso>\d{1,2})-(?P<day_iso>\d{1,2})"
)
_CHECKED = re.compile(r"(\d+) checked out of \d+")


def dates(value: str) -> list[date]:
    """Every date a cell holds, in the order written; what is no date is left out."""
    found: list[date] = []
    for match in _DATE.finditer(value):
        if match["month_name"]:
            month = _MONTHS.get(match["month_name"].lower())
            if month is None:
                continue
            year, day = int(match["year_us"]), int(match["day_us"])
        elif match["day"]:
            year, month, day = int(match["year"]), int(match["month"]), int(match["day"])
        else:
            year, month, day = (
                int(match["year_iso"]),
                int(match["month_iso"]),
                int(match["day_iso"]),
            )
        try:
            found.append(date(year, month, day))
        except ValueError:
            continue
    return found


def _text(record: AirtableRecord, names: tuple[str, ...]) -> str | None:
    value = record.text(*names)
    return None if not value or value.lower() in _NOTHING else value


def _checked(record: AirtableRecord, names: tuple[str, ...]) -> bool | None:
    """A checkbox: a boolean from the API, «N checked out of M» from a share link."""
    for name in names:
        value = record.fields.get(name)
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            match = _CHECKED.fullmatch(value.strip())
            if match:
                return int(match[1]) > 0
    return None


def details(record: AirtableRecord) -> dict[str, Any]:
    """The columns of `airtable_known_persons` beside the name, from one record.

    A person tried twice has two dates in a cell: the case is as old as the first one it
    was opened on, and the sentence that stands is the last.
    """
    born = dates(record.text(*_BIRTH_DATE_FIELDS))
    opened = dates(record.text(*_CASE_OPENED_FIELDS))
    sentenced = dates(record.text(*_SENTENCED_FIELDS))
    included = dates(record.text(*_RFM_INCLUDED_FIELDS))
    return {
        "gender": _GENDERS.get(record.text(*_GENDER_FIELDS).lower()),
        # Two birth dates in one cell are two claims, not a date.
        "birth_date": born[0] if len(born) == 1 else None,
        "region": _text(record, _REGION_FIELDS),
        "city": _text(record, _CITY_FIELDS),
        "articles": _text(record, _ARTICLES_FIELDS),
        "case_opened_on": min(opened) if opened else None,
        "sentenced_on": max(sentenced) if sentenced else None,
        "court": _text(record, _COURT_FIELDS),
        "court_card_url": _text(record, _COURT_CARD_FIELDS),
        "in_rfm": _checked(record, _IN_RFM_FIELDS),
        "rfm_included_on": min(included) if included else None,
    }
