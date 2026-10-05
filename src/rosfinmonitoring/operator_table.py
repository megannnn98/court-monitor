"""The operator's own table of the Rosfinmonitoring list, read from its public link.

Ирина keeps the list in an Airtable table of her own, «чтобы легко фильтровать по региону,
возрасту и дате добавления». It is kept by hand and is not the list: 6 000 of our entries
are not in it. It says two things neither the state nor the ОВД-Инфо copy gives us:

- the day a row was added, for entries the ОВД-Инфо copy leaves without one. It only
  *fills*: where both give a day and they differ, the ОВД-Инфо one stays, because that
  copy is a daily record and this table is typed;
- that a row was **removed** from the list. The state publishes who is on the list today
  and nothing of who was; a person the news wrote about without a name two years ago may
  have been on it then and not be now.

A removed row is a candidate for an unnamed person and nothing more. It is never an entry
of a snapshot, so nothing may say of anybody «в перечне» because of it.

The link is a setting (`RFM_TABLE_SHARE_URL`), not a constant: the repository is public
and the table is hers.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date

from sqlalchemy import delete, func, insert, select
from sqlalchemy.orm import Session

from db.orm_models import RfmOperatorEntryRecord
from rosfinmonitoring.inclusion_dates import InclusionDates, normalize_name

logger = logging.getLogger("entities")

SHARE_URL_ENV = "RFM_TABLE_SHARE_URL"
# The mark `rosfinmonitoring_entries.inclusion_source` carries for a day taken from here.
SOURCE = "operator"
ATTRIBUTION = (
    "Дата включения с пометкой «по таблице оператора» — из таблицы, которую ведёт оператор."
)

# What makes a row the row it is. A person removed and added again stands in the table
# twice, with two days; the day is therefore part of the identity.
_IDENTITY = ("ФИО", "Дата рождения", "Дата добавления", "Удаление")
# A read this much shorter than the copy we hold is not believed: the copy is replaced
# whole, and a view filtered by mistake would otherwise delete most of it.
_SHRINK_ALLOWED = 0.5
_DAY = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})")


class OperatorTableUnavailable(RuntimeError):
    """The table could not be read, or what was read is not to be trusted. The copy we
    hold stays as it was."""


@dataclass(frozen=True)
class OperatorRow:
    full_name: str
    birth_date: date | None
    birth_place: str
    added_on: date | None
    removed: bool
    kind: str
    category: str

    @property
    def normalized_name(self) -> str:
        return normalize_name(self.full_name)


def share_url(env: Mapping[str, str] | None = None) -> str:
    """The link of the table, or nothing when the operator has not set one."""
    env = os.environ if env is None else env
    return (env.get(SHARE_URL_ENV) or "").strip()


def _day(value: str) -> date | None:
    """A cell of the export as a day. The export writes the American order, 7/6/2011 for
    the sixth of July; anything else is left empty rather than guessed."""
    found = _DAY.fullmatch(value.strip())
    if found is None:
        return None
    month, day, year = (int(part) for part in found.groups())
    try:
        return date(year, month, day)
    except ValueError:
        return None


def parse_rows(rows: Iterable[Mapping[str, str]]) -> list[OperatorRow]:
    """The rows of the export; one with no name is not a row."""
    parsed = []
    for row in rows:
        # The star is the list's own mark of terrorism, not a part of the name.
        full_name = (row.get("ФИО") or "").replace("*", "").strip()
        if not full_name:
            continue
        parsed.append(
            OperatorRow(
                full_name=full_name,
                birth_date=_day(row.get("Дата рождения") or ""),
                birth_place=(row.get("Место рождения") or "").strip(" ;"),
                added_on=_day(row.get("Дата добавления") or ""),
                removed=bool((row.get("Удаление") or "").strip()),
                kind=(row.get("Calculation") or "").strip(),
                category=(row.get("категория") or "").strip(),
            )
        )
    return parsed


def read_operator_table(link: str) -> list[OperatorRow]:
    """Every row of the table behind the public link."""
    from airtable.client import AirtableError
    from airtable.share import read_records

    try:
        records = read_records(link, table="rfm_operator", identity=_IDENTITY)
    except AirtableError as exc:
        raise OperatorTableUnavailable(str(exc)) from exc
    return parse_rows(record.fields for record in records)


def read_configured_table() -> list[OperatorRow] | None:
    """The table behind the configured link; nothing when no link is set."""
    link = share_url()
    return read_operator_table(link) if link else None


def store(session: Session, rows: Sequence[OperatorRow]) -> int:
    """Replace the copy with what was just read; refuse a read too short to be the table."""
    held = session.scalar(select(func.count()).select_from(RfmOperatorEntryRecord)) or 0
    if not rows or len(rows) < held * _SHRINK_ALLOWED:
        raise OperatorTableUnavailable(
            f"прочитано строк {len(rows)}, в копии {held}: копия не тронута"
        )
    session.execute(delete(RfmOperatorEntryRecord))
    session.execute(
        insert(RfmOperatorEntryRecord),
        [
            {
                "full_name": row.full_name,
                "normalized_name": row.normalized_name,
                "birth_date": row.birth_date,
                "birth_place": row.birth_place[:512],
                "added_on": row.added_on,
                "removed": row.removed,
                "kind": row.kind[:32],
                "category": row.category[:255],
            }
            for row in rows
        ],
    )
    return len(rows)


def inclusion_dates(session: Session) -> InclusionDates:
    """The days the copy gives, by name and birth date — for entries still on the list.

    A removed row's day is the day of a listing that ended; an entry on the list now was
    added by a row that is not removed. Of two such rows of one person the later is the
    listing that stands."""
    rows = session.execute(
        select(
            RfmOperatorEntryRecord.normalized_name,
            RfmOperatorEntryRecord.birth_date,
            RfmOperatorEntryRecord.added_on,
            RfmOperatorEntryRecord.removed,
        )
    ).all()
    by_pair: dict[tuple[str, date], date] = {}
    without_day = 0
    for name, born, added_on, removed in rows:
        if added_on is None:
            without_day += 1
        if removed or added_on is None or born is None:
            continue
        key = (name, born)
        by_pair[key] = max(by_pair.get(key, added_on), added_on)
    return InclusionDates(
        by_name_and_birth=by_pair,
        total_rows=len(rows),
        rows_without_added_date=without_day,
        names_in_source=frozenset(row.normalized_name for row in rows),
    )
