"""Whether the operator's base already holds an unnamed person of the news — as a
record without a name.

The operator enters a case she cannot name as she reads of it: «41-летний житель
Ленинградской области». The result finds the same person in the same news and, having no
name to look up, said «нет в базе». A record without a name is found by what it tells
instead: the age in its name, the sex, the region or the city.

It is never certain. One record that fits is «вероятно»; several are «похожие записи», and
which one — if any — is for the operator to see. A named person is `entities.known_base`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.orm_models import AirtableKnownPersonRecord
from entities.base_unnamed import record_age
from entities.known_base import SHOWN, KnownMatch
from entities.unnamed import place_pattern

_YEAR = 365.25


@dataclass(frozen=True)
class _Record:
    name: str
    age: int
    gender: str | None
    place: str
    opened: date | None


class NamelessBase:
    """The records of the base that name nobody and tell an age."""

    def __init__(self, records: list[Any]) -> None:
        self._records = [
            _Record(
                " ".join(record.full_name.split()),
                age,
                record.gender,
                f"{record.region or ''} {record.city or ''}".lower(),
                record.case_opened_on,
            )
            for record in records
            if (age := record_age(record.full_name)) is not None
        ]

    def __len__(self) -> int:
        return len(self._records)

    @classmethod
    def from_session(cls, session: Session) -> NamelessBase:
        return cls(
            list(
                session.scalars(
                    # The active records, as `KnownBase` reads the named ones: a record
                    # the base no longer counts must not say «есть в базе».
                    select(AirtableKnownPersonRecord).where(
                        AirtableKnownPersonRecord.active,
                        AirtableKnownPersonRecord.birth_date.is_(None),
                    )
                )
            )
        )

    def match(
        self, age: int | None, gender: str, place: str, day: date | None
    ) -> KnownMatch | None:
        """The records that may be the person the news tells of: of that sex (or of a sex
        the base does not say), from that place, and of that age — counted to the day of
        the news, since a record entered two years ago tells the age of two years ago."""
        in_place = place_pattern(place)
        if age is None or in_place is None:
            return None
        # Every record is asked: one entered six years ago tells an age six years off, and
        # left out it would turn «похожие записи: 2» into «вероятно» for the other.
        found = [
            record
            for record in self._records
            if _same_age(record, age, day)
            and (not gender or record.gender in (None, gender))
            and in_place.search(record.place)
        ]
        if not found:
            return None
        names = tuple(dict.fromkeys(record.name for record in found))
        return KnownMatch("probably" if len(found) == 1 else "similar", names[:SHOWN], len(found))


def _same_age(record: _Record, age: int, day: date | None) -> bool:
    """The record's age, moved to the day of the news, is the news's age give or take a
    year: the base dates a case roughly, and a birthday falls where it falls. A record
    with no date, or news with none, is compared as told."""
    passed = (day - record.opened).days / _YEAR if day and record.opened else 0.0
    return abs((age - record.age) - passed) <= 1
