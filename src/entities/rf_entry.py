"""The entry of the Rosfinmonitoring list a person was matched to, in words.

A person may be matched to several entries; the strongest stands for them: the one with
the patronymic (`rf_check.FULL`) before a name alone, which may be a namesake. The page of
the result and both Excel files say the same thing about it, so the words stand here.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import text
from sqlalchemy.orm import Session

from entities.rf_check import FULL
from rosfinmonitoring.inclusion_dates import ATTRIBUTION
from rosfinmonitoring.operator_table import ATTRIBUTION as OPERATOR_ATTRIBUTION

# The strongest entry of each: those with the patronymic first.
_RF_ENTRIES = text(
    """
    SELECT m.group_id, m.level, e.full_name, e.birth_date, e.birth_place, e.inclusion_date,
           e.inclusion_source
    FROM entity_group_rf_matches m JOIN rosfinmonitoring_entries e ON e.id = m.entry_id
    WHERE m.group_id = ANY(:groups)
    ORDER BY m.group_id, m.level = 'full' DESC, e.full_name
    """
)

# The sheet «Источник дат» of a file that carries the days of inclusion: the operator works
# from files, so the attribution and the rule travel with the dates.
INCLUSION_NOTES = (
    "Дата включения в перечень",
    ATTRIBUTION,
    OPERATOR_ATTRIBUTION,
    (
        "Дата описывает запись перечня, а не человека: совпадение имени и даты рождения "
        "не подтверждает, что в новости речь о том же человеке."
    ),
)


@dataclass(frozen=True)
class RfEntry:
    level: str
    full_name: str
    birth_date: date | None
    birth_place: str | None
    # When this entry appeared, if the ОВД-Инфо copy says. It describes the entry, not
    # the person; see `entry_included_text`.
    inclusion_date: datetime | None
    # Empty for a day of the ОВД-Инфо copy; a name for one read elsewhere.
    inclusion_source: str | None = None

    @property
    def text(self) -> str:
        """Who the entry is: the name, the birth date and place."""
        born = f"{self.birth_date:%d.%m.%Y} г.р." if self.birth_date else ""
        return ", ".join(part for part in (self.full_name, born, self.birth_place or "") if part)


def strongest_entries(session: Session, group_ids: Sequence[int]) -> dict[int, RfEntry]:
    """The strongest entry of each of these people; nothing for one with no match."""
    found: dict[int, RfEntry] = {}
    for group_id, *entry in session.execute(_RF_ENTRIES, {"groups": list(group_ids)}):
        found.setdefault(group_id, RfEntry(*entry))
    return found


def entry_included_text(inclusion_date: datetime | None, source: str | None = None) -> str:
    """When the entry of the перечень appeared, in the words that keep it an entry's.

    «запись перечня включена 14.03.2024» and not «в перечне с 14.03.2024»: the day says
    when the list published this record, and the entry was matched to the person by name
    and birth date — which is the strongest thing we can say, and not more than that.
    `tests/rosfinmonitoring/test_inclusion_date_confirms_nobody.py` holds the rule.

    A day the ОВД-Инфо copy did not give says where it was taken from: the operator's own
    table is typed by hand, and a day from it is to be read as such.
    """
    if not inclusion_date:
        return ""
    return f"запись перечня включена {inclusion_date:%d.%m.%Y}" + source_mark(source)


def source_mark(source: str | None) -> str:
    """The words after a day that did not come from the ОВД-Инфо copy; nothing after one
    that did."""
    return " (по таблице оператора)" if source else ""


def rf_word(
    level: str | None, entry: str, inclusion_date: datetime | None, source: str | None = None
) -> str:
    """The list's word on a person; empty for one matched to no entry."""
    if level is None:
        return ""
    if level != FULL:
        # A name matched without the patronymic is a namesake: the entry's day is that
        # other person's, and it is not shown at all.
        return f"возможно тёзка: {entry}"
    # The date belongs to the entry, so it is said about the entry and never as a date of
    # the person's listing.
    included = entry_included_text(inclusion_date, source)
    return f"в перечне: {entry}" + (f", {included}" if included else "")
