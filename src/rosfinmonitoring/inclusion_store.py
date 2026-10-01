"""Writing the day of inclusion onto our entries, and keeping it there.

The date comes from the ОВД-Инфо copy of the list (`rosfinmonitoring.inclusion_dates`)
and belongs to the **entry**, not to a person: what lands in `inclusion_date` is when
that entry of the перечень appeared, and nothing downstream may read it as a fact about
whoever the entry was matched against. `tests/rosfinmonitoring/
test_inclusion_date_confirms_nobody.py` holds that rule.

Two things the writing has to get right:

- a new snapshot must not lose the dates the previous one had. The state publishes a
  snapshot without dates; the ОВД-Инфо file is read again for each, and an entry the
  file does not cover keeps whatever an earlier run established. A day that was known
  does not go back to being unknown because the list was re-downloaded;
- a name alone is not enough. `inclusion_dates.match_inclusion_date` returns nothing
  unless the name and the birth date both agree, and this module does not go around it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from db.orm_models import RosfinmonitoringEntryRecord
from rosfinmonitoring.inclusion_dates import (
    InclusionDates,
    match_inclusion_date,
    normalize_name,
)

logger = logging.getLogger("entities")

WRITE_CHUNK = 2_000

# Why an entry went without a date. The first three are the ordinary cases and are only
# worth a number; `day_month_swapped` is called out because it is the one that looks like
# a bug and is not: 126 entries differ from ours by 06.11 against 11.06 and nothing in
# either list says whether that is one person written twice or two people.
UNMATCHED_REASONS = (
    "no_birth_date",
    "source_without_birth_date",
    "name_absent_from_source",
    "day_month_swapped",
    "birth_dates_differ",
    "name_ambiguous_in_source",
)


@dataclass
class InclusionWriteResult:
    """What one write did, in the words an operator can be shown."""

    snapshot_id: int
    entries: int = 0
    dated: int = 0
    already_dated: int = 0
    # An entry the file covers but whose name matches several of their rows.
    ambiguous: int = 0
    unmatched: dict[str, int] = field(default_factory=dict)

    def summary(self) -> str:
        reasons = ", ".join(f"{name}={count}" for name, count in sorted(self.unmatched.items()))
        return (
            f"записей={self.entries} с датой={self.dated} "
            f"уже была={self.already_dated} не сопоставлено={sum(self.unmatched.values())} "
            f"({reasons or 'нет'})"
        )


def _swapped(ours: date, theirs: date) -> bool:
    """Whether the two dates are each other's day and month: 06.11 against 11.06."""
    return (ours.day, ours.month) == (theirs.month, theirs.day)


def write_inclusion_dates(
    session: Session,
    snapshot_id: int,
    dates: InclusionDates,
) -> InclusionWriteResult:
    """Put the day of inclusion on every entry of `snapshot_id` that can be given one.

    An entry already carrying a date is left alone: the file is read again on every new
    snapshot, and a day established by an earlier run is not un-established because the
    list was downloaded afresh.
    """
    result = InclusionWriteResult(snapshot_id=snapshot_id)
    entries = session.execute(
        select(
            RosfinmonitoringEntryRecord.id,
            RosfinmonitoringEntryRecord.full_name,
            RosfinmonitoringEntryRecord.birth_date,
            RosfinmonitoringEntryRecord.inclusion_date,
        ).where(RosfinmonitoringEntryRecord.snapshot_id == snapshot_id)
    ).all()
    result.entries = len(entries)

    updates: list[dict[str, object]] = []
    for entry_id, full_name, birth_date, known_date in entries:
        if known_date is not None:
            result.already_dated += 1
            continue
        day = match_inclusion_date(
            full_name, birth_date.date() if birth_date is not None else None, dates
        )
        if day is not None:
            updates.append(
                {
                    "id": entry_id,
                    "inclusion_date": datetime(day.year, day.month, day.day, tzinfo=UTC),
                }
            )
            result.dated += 1
            continue
        reason = _why_no_date(full_name, birth_date, dates)
        result.unmatched[reason] = result.unmatched.get(reason, 0) + 1
        if reason == "name_ambiguous_in_source":
            result.ambiguous += 1

    for start in range(0, len(updates), WRITE_CHUNK):
        session.execute(
            update(RosfinmonitoringEntryRecord),
            updates[start : start + WRITE_CHUNK],
        )
    logger.info(
        "event=rfm_inclusion_dates_written snapshot_id=%s %s", snapshot_id, result.summary()
    )
    return result


def _why_no_date(full_name: str, birth_date: datetime | None, dates: InclusionDates) -> str:
    """Why this entry has no date, for the count the operator is shown.

    Not a decision: the date is already refused by the time this is asked. It is a
    breakdown of the refusals, so a number that stays unexplained can be looked into.
    """
    ours = birth_date.date() if birth_date is not None else None
    name = normalize_name(full_name)
    by_name = [key for key in dates.by_name_and_birth if key[0] == name]
    if ours is None:
        return "no_birth_date"
    if not by_name:
        # They publish the name but no row of theirs carries both that name and a day we
        # could match on: either they give no birth date, or none agrees with ours.
        return (
            "source_without_birth_date"
            if name in dates.names_in_source
            else "name_absent_from_source"
        )
    if len(by_name) > 1:
        return "name_ambiguous_in_source"
    theirs = by_name[0][1]
    if theirs == date.min:
        return "source_without_birth_date"
    if _swapped(ours, theirs):
        return "day_month_swapped"
    return "birth_dates_differ"
