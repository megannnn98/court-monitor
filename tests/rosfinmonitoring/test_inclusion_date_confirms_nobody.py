"""The date of inclusion belongs to the list's entry, not to the person in the case.

The published list gives each entry a date of inclusion. That date describes the entry:
it says when that entry appeared in the list, and says nothing about whether the person
our news are about is the one in that entry. The only thing our comparison checks is the
name, patronymic included — the news carry no birth date, so there is nothing to compare
one against.

So a full name match plus an inclusion date is not confirmation. It is a name match whose
match has a date attached, and the honest form of that is «probable, since the day we
first saw it» — our own lower bound, never the list's date, which would put a date on a
person the comparison never identified.

`test_the_list_s_own_date_makes_the_status_confirmed` in the session's own
`test_rfm_membership.py` asserts the opposite: that the list's date alone confirms. The
two cannot both hold. This file holds the rule; that one will have to go.
"""

from __future__ import annotations

import os
import time
from datetime import UTC, date, datetime

import pytest

membership_module = pytest.importorskip(
    "rosfinmonitoring.membership",
    reason=(
        "rosfinmonitoring.membership is not in the repository yet. This rule is not"
        " enforced until it lands: a full-name match plus a date of inclusion will then"
        " be checked against it."
    ),
)

matched = membership_module.matched
RfmStatus = membership_module.RfmStatus

FIRST_SYNC = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
SECOND_SYNC = datetime(2026, 9, 30, 6, 0, tzinfo=UTC)

# The entry entered the list on this day. It is the entry's day.
INCLUSION_DATE = date(2024, 3, 14)


@pytest.fixture(autouse=True)
def _utc(monkeypatch: pytest.MonkeyPatch) -> None:
    """The day of a moment is read in local time, and there is no time of day that is safe
    everywhere.

    Offsets run from -12 to +14, which is 26 hours: wider than a day, so any single
    instant crosses midnight in some zone or other. Noon UTC gives 2026-09-02 in Auckland
    and 2026-09-01 in Moscow. The rule under test says nothing about the zone, so the
    zone is pinned here rather than left to the machine: without this the test would fail
    on a CI box east of UTC+12 for a reason that has nothing to do with the rule.
    """
    monkeypatch.setenv("TZ", "UTC")
    time.tzset()
    os.environ["TZ"] = "UTC"


def test_a_list_inclusion_date_alone_does_not_confirm_the_person() -> None:
    """Everything the comparison can establish, established: the whole name is the list's.

    No birth date or birthplace from the case was matched — none is available to match,
    since the news carry no birth date. Under that condition the result is probable,
    however old the entry is and however long it has been on the list.
    """
    result = matched(
        certain=True,
        inclusion_date=INCLUSION_DATE,
        first_seen_at=FIRST_SYNC,
        last_seen_at=SECOND_SYNC,
    )

    assert result.status is RfmStatus.PROBABLE, (
        "a date of inclusion describes the list's entry, not the person; with nothing but"
        " the name matched it cannot confirm anyone"
    )


def test_the_date_shown_is_ours_and_not_the_lists() -> None:
    """The day we first saw the entry is a lower bound and belongs to us.

    The list's date would be more precise and wrong: it belongs to an entry found by
    name, and attaching it to the person in the case says we identified them when all we
    did was read a name.
    """
    result = matched(
        certain=True,
        inclusion_date=INCLUSION_DATE,
        first_seen_at=FIRST_SYNC,
        last_seen_at=SECOND_SYNC,
    )

    assert result.since == date(2026, 9, 1), "must be the day we first saw them ourselves"
    assert result.since != INCLUSION_DATE, "the entry's day of inclusion is not our person's day"


def test_no_availability_of_the_case_identifier_is_not_consent_to_confirm() -> None:
    """A date of inclusion and a match on everything available is still everything available.

    Written separately because it is the case that looks safe: the name is complete, the
    entry has a date, nothing is missing from the entry. What is missing is the one thing
    that would identify the person, and its absence is not a formality to be waived when
    the rest is in order.
    """
    without_entry_date = matched(
        certain=True,
        inclusion_date=None,
        first_seen_at=FIRST_SYNC,
        last_seen_at=SECOND_SYNC,
    )
    with_entry_date = matched(
        certain=True,
        inclusion_date=INCLUSION_DATE,
        first_seen_at=FIRST_SYNC,
        last_seen_at=SECOND_SYNC,
    )

    assert without_entry_date.status is with_entry_date.status, (
        "the entry's date of inclusion changed the verdict; it must not be able to"
    )
    assert with_entry_date.status is not RfmStatus.CONFIRMED
