"""What the comparison may say about a person, and on what basis.

`test_inclusion_date_confirms_nobody.py` holds the rule that a day of inclusion does not
confirm anybody. These are the cases around it: what does confirm, and what the module
says when the comparison never ran.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from rosfinmonitoring.membership import (
    RfmStatus,
    matched,
    unmatched,
)

FIRST_SEEN = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
LAST_SEEN = datetime(2026, 9, 30, 6, 0, tzinfo=UTC)
INCLUSION = date(2024, 3, 14)


def test_a_name_and_nothing_else_is_probable_whatever_the_entry_carries() -> None:
    """The whole entry is filled in and the case still carries a name. What is missing is
    the one thing that would identify a person, and a full entry is not a substitute."""
    result = matched(
        certain=True,
        inclusion_date=INCLUSION,
        first_seen_at=FIRST_SEEN,
        last_seen_at=LAST_SEEN,
        entry_birth_date=date(1980, 5, 1),
        entry_birth_place="Г. МОСКВА",
    )

    assert result.status is RfmStatus.PROBABLE
    assert result.since == date(2026, 9, 1)


def test_only_the_birth_date_and_the_place_together_identify_a_person() -> None:
    """This is the way to `confirmed`, and it is written down so that reaching it is a
    deliberate step rather than something a name quietly becomes."""
    result = matched(
        certain=True,
        inclusion_date=INCLUSION,
        first_seen_at=FIRST_SEEN,
        last_seen_at=LAST_SEEN,
        entry_birth_date=date(1980, 5, 1),
        entry_birth_place="Г. Москва",
        case_birth_date=date(1980, 5, 1),
        case_birth_place="г. москва",
    )

    assert result.status is RfmStatus.CONFIRMED, (
        "the entry's own birth date and place, agreeing with the case's, are what a name "
        "cannot supply"
    )


def test_the_birth_date_alone_does_not_identify_anyone() -> None:
    """Every person has a birthday; the place is what makes the pair specific, and one of
    the two is not «nearly» enough."""
    result = matched(
        certain=True,
        inclusion_date=INCLUSION,
        first_seen_at=FIRST_SEEN,
        last_seen_at=LAST_SEEN,
        entry_birth_date=date(1980, 5, 1),
        entry_birth_place="Г. МОСКВА",
        case_birth_date=date(1980, 5, 1),
    )

    assert result.status is RfmStatus.PROBABLE


def test_a_birth_date_that_differs_identifies_nobody() -> None:
    result = matched(
        certain=True,
        inclusion_date=INCLUSION,
        first_seen_at=FIRST_SEEN,
        last_seen_at=LAST_SEEN,
        entry_birth_date=date(1980, 5, 1),
        entry_birth_place="Г. МОСКВА",
        case_birth_date=date(1975, 9, 9),
        case_birth_place="Г. МОСКВА",
    )

    assert result.status is RfmStatus.PROBABLE


def test_a_namesake_carries_no_day_of_inclusion() -> None:
    """The entry found without the patronymic is somebody else's record, and its day is
    that person's. It is not even carried here, so that no caller can print it."""
    result = matched(
        certain=False,
        inclusion_date=INCLUSION,
        first_seen_at=FIRST_SEEN,
        last_seen_at=LAST_SEEN,
    )

    assert result.status is RfmStatus.PROBABLE
    assert result.entry_included_on is None


def test_the_list_s_own_day_is_carried_as_the_entry_s_and_not_as_a_since() -> None:
    """It has to be reachable — «запись перечня включена …» is printed from it — and it
    has to be named after the entry, because that is what it is."""
    result = matched(
        certain=True,
        inclusion_date=INCLUSION,
        first_seen_at=FIRST_SEEN,
        last_seen_at=LAST_SEEN,
    )

    assert result.entry_included_on == INCLUSION
    assert result.since == date(2026, 9, 1)
    assert result.since != result.entry_included_on


def test_a_datetime_of_inclusion_is_read_as_the_day_it_names() -> None:
    result = matched(
        certain=True,
        inclusion_date=datetime(2024, 3, 14, 7, 0, tzinfo=UTC),
        first_seen_at=FIRST_SEEN,
        last_seen_at=LAST_SEEN,
    )

    assert result.entry_included_on == INCLUSION
    assert not isinstance(result.entry_included_on, datetime)


def test_a_check_that_never_ran_says_nothing() -> None:
    """Matches are rewritten whole at every check, so an empty table reads the same for
    «nobody is in the list» and for «nobody looked»."""
    assert unmatched(checked=False).status is RfmStatus.UNKNOWN
    assert unmatched(checked=True).status is RfmStatus.NOT_FOUND


def test_unmatched_carries_no_dates_at_all() -> None:
    result = unmatched(checked=True)

    assert result.since is None
    assert result.entry_included_on is None
    assert result.first_seen_at is None
    assert result.last_seen_at is None
