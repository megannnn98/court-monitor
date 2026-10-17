"""What we may say about a person in a case, with respect to the перечень.

The list gives an entry a name, a birth date, a birthplace and — thanks to the ОВД-Инфо
copy — a day of inclusion. Our news carry none of that: a name and nothing else. So the
only thing a comparison between a case and the list can ever establish, on its own, is
that a name is in the list.

That is the whole reason this module exists, and the reason it is small. Two things that
look alike must not be swapped:

- the **entry's** day of inclusion, which says when that record of the list appeared;
- the day **we** first saw the entry among the snapshots we downloaded, which is a lower
  bound on that and the only day that is ours to state.

`since` is always the second. A date of inclusion attached to a person the comparison
never identified says we know when they entered a list, and we do not.
`tests/rosfinmonitoring/test_inclusion_date_confirms_nobody.py` holds this rule and every
test in it is written against this module's `matched`.

`entry_included_on` is the one place the list's own day may be read, and it is named after
the entry rather than the person on purpose: it is what «запись перечня включена …» is
printed from.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum


class RfmStatus(StrEnum):
    """How much the comparison established about a person.

    `not_found` and `unknown` are different, which they were not: matches are rewritten
    whole at every check, so an empty table reads the same for «nobody matched» and for
    «nobody looked».
    """

    CONFIRMED = "confirmed"
    """The entry's birth date and birthplace are the case's, which is what identifies a
    person beyond the name. The news carry no such fields, so nothing reaches this from
    them — it is here so that the way to it is written down."""

    PROBABLE = "probable"
    """The name is in the list and nothing more was established. A name and a date of
    inclusion together are still this: the date belongs to the entry, not to the person."""

    NOT_FOUND = "not_found"
    """Compared with the list, and the name is not in it."""

    UNKNOWN = "unknown"
    """Never compared, or the comparison cannot be read."""


@dataclass(frozen=True)
class RfmMembership:
    """What one person may be said to be, and on what basis."""

    status: RfmStatus
    # Our own lower bound: the day we first saw the entry. Never the list's date.
    since: date | None
    # The list's own day for the entry we matched, when the ОВД-Инфо copy gives one. It
    # describes the entry; see the module docstring before printing it.
    entry_included_on: date | None = None
    first_seen_at: datetime | None = None
    last_seen_at: datetime | None = None


def _day(moment: datetime | date | None) -> date | None:
    if moment is None:
        return None
    # A datetime is a date as far as isinstance is concerned: check it first.
    if isinstance(moment, datetime):
        return moment.astimezone().date() if moment.tzinfo else moment.date()
    return moment


def unmatched(*, checked: bool) -> RfmMembership:
    """No entry of the list is this person. A check that never ran says nothing."""
    return RfmMembership(
        RfmStatus.NOT_FOUND if checked else RfmStatus.UNKNOWN, None, None, None, None
    )


def matched(
    *,
    certain: bool,
    inclusion_date: datetime | date | None,
    first_seen_at: datetime | None,
    last_seen_at: datetime | None,
    entry_birth_date: date | None = None,
    entry_birth_place: str | None = None,
    case_birth_date: date | None = None,
    case_birth_place: str | None = None,
) -> RfmMembership:
    """The person is in the list, as far as the comparison could tell.

    `certain` is whether the whole name, patronymic included, is the list's. When it is
    not, the entry belongs to a namesake: its day of inclusion is that namesake's, and
    saying otherwise would put a date on the wrong person.

    `confirmed` needs the one thing a name cannot supply — the entry's birth date and
    birthplace matching the case's. The news carry neither, so from them this is always
    `probable`, and that is not a shortfall to be papered over: it is the honest reading.
    """
    if not certain:
        return RfmMembership(
            RfmStatus.PROBABLE, _day(first_seen_at), None, first_seen_at, last_seen_at
        )
    identified = (
        case_birth_date is not None
        and case_birth_date == entry_birth_date
        and case_birth_place is not None
        and case_birth_place.casefold() == (entry_birth_place or "").casefold()
    )
    return RfmMembership(
        RfmStatus.CONFIRMED if identified else RfmStatus.PROBABLE,
        _day(first_seen_at),
        _day(inclusion_date),
        first_seen_at,
        last_seen_at,
    )
