"""Who on the Rosfinmonitoring list a nameless record of the operator's base may be.

The operator enters a case when the news of it comes, often without a name: «34-летний
уроженец Крыма». Months later the person appears on the list, with a name, a birth date
and a birth place. So each nameless record is put next to the entries of the list of that
age on the day the case was opened, of that sex and born in that place; those included in
the list after the case was opened stand first. The operator confirms: this is who to look
at.

The other direction — a news item that names nobody, against the named people of the base
— is `entities.base_candidates`.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from sqlalchemy import delete, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from db.orm_models import AirtableKnownPersonRecord, UnnamedDecisionRecord
from entities.unnamed import DIFFERENT, SAME, place_pattern, place_stems

SHOWN = 5
DECISIONS = frozenset({SAME, DIFFERENT})

_AGE = re.compile(r"(\d{1,3})-?\s?летн", re.IGNORECASE)
# The sex an entry's patronymic tells; the list's names are upper case, some end with «*».
_MALE = re.compile(r"(ВИЧ|ОГЛЫ|УГЛИ|ИЧ)\*?\s*$")
_FEMALE = re.compile(r"(ВНА|КЫЗЫ|ГЫЗЫ|ИЧНА)\*?\s*$")


def record_age(full_name: str) -> int | None:
    """The age a nameless record's name tells: «34-летний уроженец Крыма» → 34."""
    match = _AGE.search(full_name)
    return int(match[1]) if match else None


def age_on(day: date, born: date) -> int:
    return day.year - born.year - ((day.month, day.day) < (born.month, born.day))


def entry_gender(full_name: str) -> str | None:
    if _FEMALE.search(full_name):
        return "female"
    if _MALE.search(full_name):
        return "male"
    return None


@dataclass
class RfEntry:
    full_name: str
    normalized_name: str
    birth_date: date
    birth_place: str
    included_on: date | None
    # Not on the list now: a row the operator's table marks as removed from it.
    removed: bool = False
    # Read once for the whole list: every record is compared with thousands of entries.
    gender: str | None = field(init=False)
    place: str = field(init=False)

    def __post_init__(self) -> None:
        self.gender = entry_gender(self.full_name)
        self.place = self.birth_place.lower()

    @property
    def key(self) -> str:
        """The entry by who it is: its id changes with every snapshot."""
        return f"{self.normalized_name}|{self.birth_date.isoformat()}"


class RfList:
    """The latest snapshot of the list, by the year of birth."""

    def __init__(self, entries: Iterable[RfEntry]) -> None:
        self._by_year: dict[int, list[RfEntry]] = defaultdict(list)
        for entry in entries:
            self._by_year[entry.birth_date.year].append(entry)

    @classmethod
    def from_session(cls, session: Session) -> RfList:
        rows = session.execute(
            text(
                """
                WITH listed AS (
                    SELECT full_name, normalized_name, birth_date::date AS birth_date,
                           coalesce(birth_place, '') AS birth_place,
                           inclusion_date::date AS included_on, false AS removed
                    FROM rosfinmonitoring_entries
                    WHERE snapshot_id = (SELECT max(id) FROM rosfinmonitoring_snapshots)
                      AND birth_date IS NOT NULL
                ),
                -- Who was on the list and is not now, by the operator's own table: the
                -- record may be of a case older than the removal.
                removed AS (
                    SELECT DISTINCT ON (o.normalized_name, o.birth_date)
                           o.full_name, o.normalized_name, o.birth_date, o.birth_place,
                           o.added_on AS included_on, true AS removed
                    FROM rfm_operator_entries o
                    WHERE o.removed AND o.birth_date IS NOT NULL
                      AND NOT EXISTS (
                          SELECT 1 FROM listed l
                          WHERE l.normalized_name = o.normalized_name
                            AND l.birth_date = o.birth_date)
                    ORDER BY o.normalized_name, o.birth_date, o.added_on DESC NULLS LAST
                )
                SELECT * FROM listed UNION ALL SELECT * FROM removed
                """
            )
        ).all()
        return cls(RfEntry(*row) for row in rows)

    def born_in(self, years: Iterable[int]) -> list[RfEntry]:
        return [entry for year in years for entry in self._by_year.get(year, [])]


@dataclass
class RfCandidate:
    entry: RfEntry
    age: int
    reasons: list[str] = field(default_factory=list)
    city_match: bool = False
    # Included in the list before the case was opened.
    earlier: bool = False
    decision: str | None = None


@dataclass
class NamelessCase:
    """A nameless record of the base with the entries of the list it may be."""

    record: AirtableKnownPersonRecord
    # None for a record confirmed earlier whose name no longer tells an age.
    age: int | None
    candidates: list[RfCandidate]
    # How many fit in all; `candidates` are the first `SHOWN` of them.
    total: int
    # The entry a person said the record is (its name and birth date), whether or not it
    # is among the candidates now: an entry leaves the list, a word on it stays.
    confirmed: str | None = None

    @property
    def identified(self) -> RfCandidate | None:
        return next((item for item in self.candidates if item.decision == SAME), None)

    @property
    def open(self) -> list[RfCandidate]:
        return [item for item in self.candidates if item.decision is None]

    @property
    def newest(self) -> date:
        """When the latest of the candidates still to look at came onto the list: a record
        with a fresh one stands first."""
        return max(
            (
                item.entry.included_on
                for item in self.open
                if item.entry.included_on and not item.earlier
            ),
            default=date.min,
        )


def nameless_records(session: Session) -> list[AirtableKnownPersonRecord]:
    """The records to identify: no birth date, but an age in the name («34-летний уроженец
    Крыма») and the day the case was opened — without those there is nothing to compare
    an entry's birth date with."""
    rows = session.scalars(
        select(AirtableKnownPersonRecord).where(
            AirtableKnownPersonRecord.birth_date.is_(None),
            AirtableKnownPersonRecord.case_opened_on.is_not(None),
        )
    )
    return [row for row in rows if record_age(row.full_name) is not None]


class _Place:
    """A place to look for in thousands of birth places: the plain substring first, the
    word boundary only where that found something."""

    def __init__(self, place: str) -> None:
        self._stems = place_stems(place)
        self._pattern = place_pattern(place)

    def within(self, text: str) -> bool:
        for stem in self._stems:
            if stem in text:
                return self._pattern is not None and self._pattern.search(text) is not None
        return False


def candidates_for(
    record: Any, rf_list: RfList, words: dict[str, str], *, shown: int = SHOWN
) -> NamelessCase | None:
    """The entries that may be this record, or None for a record that tells no age.

    The record's age is the news's, and the case was opened before the news: on the day
    it was opened the person was that age or a year younger; a year older allows for a
    date the base only knows roughly. An entry included before the case was opened is
    hardly someone this case put on the list, but the base may date the case late: such
    an entry stands below the others and says so."""
    age = record_age(record.full_name)
    opened: date | None = record.case_opened_on
    if age is None or opened is None:
        return None
    city = _Place(record.city or "")
    region = _Place(record.region or "")
    # Read once: the record is an ORM row, and the loop below runs over thousands of
    # entries for each of hundreds of records.
    gender = record.gender
    found: list[RfCandidate] = []
    for entry in rf_list.born_in(range(opened.year - age - 2, opened.year - age + 2)):
        if gender and entry.gender not in (None, gender):
            continue
        # The place before the age: it turns away nearly everyone, and costs least.
        city_match = city.within(entry.place)
        if not city_match and not region.within(entry.place):
            continue
        entry_age = age_on(opened, entry.birth_date)
        if abs(entry_age - age) > 1:
            continue
        earlier = bool(entry.included_on and entry.included_on < opened)
        reasons = [
            f"{entry_age} лет на {opened:%d.%m.%Y}",
            f"родился: {entry.birth_place}",
            f"включён в перечень {entry.included_on:%d.%m.%Y}, до возбуждения дела"
            if earlier
            else f"включён в перечень {entry.included_on:%d.%m.%Y}, после возбуждения дела"
            if entry.included_on
            else "дата включения в перечень неизвестна",
        ]
        if entry.removed:
            reasons.append("позже исключён из перечня — по таблице оператора")
        found.append(
            RfCandidate(entry, entry_age, reasons, city_match, earlier, words.get(entry.key))
        )
    # Confirmed first, the rejected last; then those included after the case was opened,
    # born in the very city, the exact age, and the latest onto the list.
    found.sort(
        key=lambda item: (
            item.decision != SAME,
            item.decision == DIFFERENT,
            item.earlier,
            not item.city_match,
            item.age != age,
            -(item.entry.included_on or date.min).toordinal(),
            item.entry.full_name,
        )
    )
    confirmed = next((key for key, decision in words.items() if decision == SAME), None)
    return NamelessCase(record, age, found[:shown], len(found), confirmed)


def nameless_cases(session: Session) -> list[NamelessCase]:
    """Every nameless record that has a candidate, the one with the freshest first."""
    records = nameless_records(session)
    rf_list = RfList.from_session(session)
    by_record: dict[str, dict[str, str]] = defaultdict(dict)
    for figurant_key, candidate, decision in session.execute(
        select(
            UnnamedDecisionRecord.figurant_key,
            UnnamedDecisionRecord.candidate,
            UnnamedDecisionRecord.decision,
        ).where(UnnamedDecisionRecord.figurant_key.in_({row.external_id for row in records}))
    ):
        by_record[figurant_key][candidate] = decision
    cases = [
        case
        for record in records
        if (case := candidates_for(record, rf_list, by_record[record.external_id])) is not None
        and (case.candidates or case.confirmed)
    ]
    cases.extend(_confirmed_elsewhere(session, {row.external_id for row in records}))
    cases.sort(key=lambda case: (-case.newest.toordinal(), case.record.full_name))
    return cases


def _confirmed_elsewhere(session: Session, compared: set[str]) -> list[NamelessCase]:
    """The records a person identified that can no longer be compared with the list — the
    age left the name, or the day the case was opened left the base — but still carry no
    birth date: the word on them is shown, not lost. A record that got its birth date is
    a named person now, and is nobody to identify."""
    rows = session.execute(
        select(AirtableKnownPersonRecord, UnnamedDecisionRecord.candidate)
        .join(
            UnnamedDecisionRecord,
            UnnamedDecisionRecord.figurant_key == AirtableKnownPersonRecord.external_id,
        )
        .where(
            UnnamedDecisionRecord.decision == SAME,
            AirtableKnownPersonRecord.birth_date.is_(None),
        )
    ).all()
    return [
        NamelessCase(record, record_age(record.full_name), [], 0, candidate)
        for record, candidate in rows
        if record.external_id not in compared
    ]


def say(session: Session, record_id: str, candidate: str, decision: str | None) -> None:
    """A person's word on a record and an entry: it is them, it is not, or (None) no word.

    Kept where the words on the unnamed of the news are (`unnamed_decisions`), by the
    record's id in the base and the entry's name and birth date. One entry is the record
    at most: «это он» takes the word back from any other."""
    if decision is not None and decision not in DECISIONS:
        raise ValueError(f"unknown decision: {decision}")
    if decision is None or decision == SAME:
        session.execute(
            delete(UnnamedDecisionRecord).where(
                UnnamedDecisionRecord.figurant_key == record_id,
                (UnnamedDecisionRecord.candidate == candidate)
                if decision is None
                else (UnnamedDecisionRecord.decision == SAME),
            )
        )
    if decision is None:
        return
    statement = pg_insert(UnnamedDecisionRecord).values(
        figurant_key=record_id, candidate=candidate, decision=decision
    )
    session.execute(
        statement.on_conflict_do_update(
            index_elements=["figurant_key", "candidate"],
            set_={"decision": decision, "decided_at": text("now()")},
        )
    )


def counts(cases: Sequence[NamelessCase]) -> dict[str, int]:
    found = sum(case.confirmed is not None for case in cases)
    waiting = sum(case.confirmed is None and bool(case.open) for case in cases)
    return {"open": waiting, "found": found, "all": len(cases)}
