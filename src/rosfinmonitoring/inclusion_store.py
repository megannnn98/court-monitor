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
from sqlalchemy.orm import Session, sessionmaker

from db.orm_models import (
    RosfinmonitoringEntryRecord,
    RosfinmonitoringSnapshotRecord,
)
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


def backfill_birth_dates(session: Session, snapshot_id: int) -> int:
    """Re-read the birth date out of the stored publication line, where it is missing.

    A snapshot imported before the parser understood the former names in brackets holds
    828 entries whose date of birth ended up inside the birth place, and 795 of those
    have no birth date at all. Those entries can never be told from a namesake, and a
    namesake is exactly what a day of inclusion must not be given to.

    The whole published line is kept in `raw_data`, so nothing is fetched: the same
    parser that reads the page reads the line, and only the fields that are missing are
    filled. Full names and matching keys are not touched — a day of birth does not change
    who the entry is about.
    """
    from rosfinmonitoring.parser import read_published_line

    updates: list[dict[str, object]] = []
    former: dict[int, list[str]] = {}
    for entry_id, line in session.execute(
        select(RosfinmonitoringEntryRecord.id, RosfinmonitoringEntryRecord.raw_data).where(
            RosfinmonitoringEntryRecord.snapshot_id == snapshot_id,
            RosfinmonitoringEntryRecord.birth_date.is_(None),
        )
    ).all():
        text = (line or {}).get("entry")
        if not isinstance(text, str) or not text:
            continue
        parsed = read_published_line(text)
        if parsed is None or parsed.birth_date is None:
            continue
        updates.append(
            {
                "id": entry_id,
                "birth_date": parsed.birth_date,
                "birth_place": parsed.birth_place,
            }
        )
        if parsed.raw_data.get("former_names"):
            former[entry_id] = list(parsed.raw_data["former_names"])

    for start in range(0, len(updates), WRITE_CHUNK):
        chunk = updates[start : start + WRITE_CHUNK]
        session.execute(update(RosfinmonitoringEntryRecord), chunk)
    # The former names travel with them: our own record of the name the entry was
    # published under, from the same line. Nothing reads them yet.
    for entry_id, names in former.items():
        record = session.get(RosfinmonitoringEntryRecord, entry_id)
        if record is not None:
            record.raw_data = {**(record.raw_data or {}), "former_names": names}
    logger.info(
        "event=rfm_birth_dates_backfilled snapshot_id=%s dates=%d", snapshot_id, len(updates)
    )
    return len(updates)


def carry_dates_from_previous_snapshot(
    session_factory: sessionmaker[Session], snapshot_id: int
) -> int:
    """Copy the days of inclusion from the snapshot before this one, onto this one's.

    The list is re-downloaded daily and only becomes a new snapshot when it changed, so
    consecutive snapshots hold nearly the same entries. A day established for one of them
    is a day for the same person now, and the state's copy carries no dates of its own —
    without this, a snapshot imported on a day the ОВД-Инфо file could not be read would
    have none, and the page that filters by them would stay empty until the state next
    changed the list.

    Matched on the name and the birth date, as everywhere else. The day belongs to the
    entry, and a name on its own would carry one person's day onto a namesake's row.
    """
    # One session throughout: this runs inside a check, and leaving sessions open is how a
    # pool runs out and a check waits for a connection that never arrives.
    with session_factory() as session:
        previous_id = session.execute(
            select(RosfinmonitoringSnapshotRecord.id)
            .where(RosfinmonitoringSnapshotRecord.id < snapshot_id)
            .order_by(RosfinmonitoringSnapshotRecord.id.desc())
            .limit(1)
        ).scalar_one_or_none()
        if previous_id is None:
            return 0

        # The day and where it came from travel together: a day of the operator's table
        # must not turn into one of the ОВД-Инфо copy by being carried.
        known: dict[tuple[str, date], tuple[date, str | None]] = {}
        for full_name, birth_date, inclusion_date, source in session.execute(
            select(
                RosfinmonitoringEntryRecord.full_name,
                RosfinmonitoringEntryRecord.birth_date,
                RosfinmonitoringEntryRecord.inclusion_date,
                RosfinmonitoringEntryRecord.inclusion_source,
            ).where(
                RosfinmonitoringEntryRecord.snapshot_id == previous_id,
                RosfinmonitoringEntryRecord.inclusion_date.isnot(None),
            )
        ).all():
            if birth_date is None:
                continue
            known[normalize_name(full_name), birth_date.date()] = (inclusion_date.date(), source)

        updates: list[dict[str, object]] = []
        for entry_id, full_name, birth_date in session.execute(
            select(
                RosfinmonitoringEntryRecord.id,
                RosfinmonitoringEntryRecord.full_name,
                RosfinmonitoringEntryRecord.birth_date,
            ).where(
                RosfinmonitoringEntryRecord.snapshot_id == snapshot_id,
                RosfinmonitoringEntryRecord.inclusion_date.is_(None),
                RosfinmonitoringEntryRecord.birth_date.isnot(None),
            )
        ).all():
            if birth_date is None:
                continue
            carried = known.get((normalize_name(full_name), birth_date.date()))
            if carried is None:
                continue
            day, source = carried
            updates.append(
                {
                    "id": entry_id,
                    "inclusion_date": datetime(day.year, day.month, day.day, tzinfo=UTC),
                    "inclusion_source": source,
                }
            )
        for start in range(0, len(updates), WRITE_CHUNK):
            session.execute(
                update(RosfinmonitoringEntryRecord), updates[start : start + WRITE_CHUNK]
            )
        # Committed here: this session is not a `begin()` block, and closing one only
        # rolls back — the count would then report days that were never written.
        session.commit()
    logger.info(
        "event=rfm_inclusion_dates_carried from_snapshot=%s into_snapshot=%s dates=%d",
        previous_id,
        snapshot_id,
        len(updates),
    )
    return len(updates)


def write_inclusion_dates(
    session: Session,
    snapshot_id: int,
    dates: InclusionDates,
    *,
    source: str | None = None,
) -> InclusionWriteResult:
    """Put the day of inclusion on every entry of `snapshot_id` that can be given one.

    `source` is written beside the day: empty for the ОВД-Инфо copy, a name for any other
    place the days were read from.

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
            RosfinmonitoringEntryRecord.inclusion_source,
        ).where(RosfinmonitoringEntryRecord.snapshot_id == snapshot_id)
    ).all()
    result.entries = len(entries)

    updates: list[dict[str, object]] = []
    for entry_id, full_name, birth_date, known_date, known_source in entries:
        # A day another source filled in is a stand-in: the ОВД-Инфо copy, which keeps the
        # list's history day by day, replaces it the moment it has a day of its own.
        stand_in = known_date is not None and known_source is not None and source is None
        if known_date is not None and not stand_in:
            result.already_dated += 1
            continue
        day = match_inclusion_date(
            full_name, birth_date.date() if birth_date is not None else None, dates
        )
        if day is None and stand_in:
            result.already_dated += 1
            continue
        if day is not None:
            updates.append(
                {
                    "id": entry_id,
                    "inclusion_date": datetime(day.year, day.month, day.day, tzinfo=UTC),
                    "inclusion_source": source,
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
