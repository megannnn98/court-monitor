"""When each entry of the перечень was included, from the ОВД-Инфо copy of the list.

The published list (fedsfm.ru) says who is in it and never says since when: the column
`inclusion_date` on our entries is empty for all of them. ОВД-Инфо rebuilds the same list
daily and keeps a per-person history, so their copy carries an `added_date` for 31 369 of
its 31 390 rows — the whole span from 2011 to today, not a recent window.

What this module produces is the date of an **entry of the list**. It is not a fact about
a person: matching one of our entries to one of theirs needs the name and the birth date
to agree, and even then what has been established is that two records describe the same
person, not that the person in a news item is that one. `tests/rosfinmonitoring/
test_inclusion_date_confirms_nobody.py` holds that rule and nothing here may weaken it.

The file is a build artefact of someone else's site, not a documented API: the name
carries a content hash that changes on every rebuild, and the columns can change without
notice. So the reader checks the schema it needs and **refuses** anything else, rather than
reading a column that has been renamed into something that looks similar.
"""

from __future__ import annotations

import io
import logging
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Final

import httpx

logger = logging.getLogger("entities")

# The page the list is published on. Its HTML names the parquet file it is built from,
# which is the only stable way to find it: the file name itself changes on every rebuild.
PUBLISHED_PAGE_URL: Final = "https://repression.net/rosfinmonitoring"

# The registerFile call that ties the logical name to the built file.
_FILE_REFERENCE: Final = re.compile(
    r'registerFile\("\./data/rfm\.parquet",\s*\{[^{}]*"path":\s*"\./(_file/[^"]+)"'
)

# What we ask the file for. A name and a birth date to match on, a day to write down.
_REQUIRED_COLUMNS: Final = ("name", "birth_date", "added_date")

_PARQUET_MAGIC: Final = b"PAR1"

# The list ОВД-Инфо publish is licensed CC BY 3.0, so wherever a date of theirs is shown
# to an operator the attribution travels with it. See `ATTRIBUTION`.
ATTRIBUTION: Final = (
    "Дата включения — по списку ОВД-Инфо (CC BY 3.0): repression.net/rosfinmonitoring"
)


class InclusionDatesUnavailable(RuntimeError):
    """The ОВД-Инфо copy of the list could not be read, for any reason.

    Its own type so that a caller catching a failed read catches *this* and not every
    other error in the process: the promise that a failed read leaves the last known
    dates in place is only as good as the handler.
    """


@dataclass(frozen=True)
class InclusionDates:
    """The dates of inclusion, keyed by the name and birth date they belong to.

    An entry is keyed by the pair, never by the name alone: the list holds namesakes, and
    a date written against the wrong one of two people is worse than no date at all.
    """

    by_name_and_birth: dict[tuple[str, date], date]
    total_rows: int
    rows_without_added_date: int
    # Every name the file publishes, dated or not. 165 of their rows carry no birth date
    # and so can never be matched on one; the name alone is not a match, and the reason an
    # entry went without a date is worth telling apart from «they do not publish this
    # person at all».
    names_in_source: frozenset[str] = frozenset()


def normalize_name(name: str) -> str:
    """The same normalisation the list parser uses, so both sides compare equal.

    Kept as a copy of `parser._normalize_name` rather than an import: this module reads
    someone else's file, and it must not change what the state's own page parses into.
    """
    value = name.strip().lower()
    value = re.sub(r"[ё]", "е", value)
    value = re.sub(r"[\s\-\.]+", " ", value)
    value = re.sub(r"[^а-яa-z\s]", "", value)
    return value.strip()


def file_url_from_page(page: str) -> str:
    """The parquet's URL, read out of the page the list is published on.

    The file name carries a content hash, so there is no fixed address to hard-code; the
    page is where the current one is written down.
    """
    match = _FILE_REFERENCE.search(page)
    if match is None:
        raise InclusionDatesUnavailable(
            f"{PUBLISHED_PAGE_URL} не ссылается на файл со списком. "
            "Структура страницы изменилась — даты не тронуты."
        )
    return f"https://repression.net/{match.group(1)}"


def read_inclusion_dates(raw: bytes) -> InclusionDates:
    """Every `added_date` in the file, keyed by the name and birth date it belongs to.

    Raises rather than returning what it can when the file is not the list or no longer
    has the columns we match on: a partial read would write dates against the wrong
    records, which is the one thing this whole exercise must not do.
    """
    if not raw.startswith(_PARQUET_MAGIC):
        raise InclusionDatesUnavailable(
            "Файл со списком ОВД-Инфо не parquet. Либо это не он, либо сменился формат — "
            "даты не тронуты."
        )

    from pyarrow import parquet

    try:
        table = parquet.read_table(io.BytesIO(raw))
    except Exception as exc:  # a corrupt or truncated file reads the same here
        raise InclusionDatesUnavailable(f"файл со списком ОВД-Инфо не читается: {exc}") from exc

    missing = [column for column in _REQUIRED_COLUMNS if column not in table.column_names]
    if missing:
        raise InclusionDatesUnavailable(
            "В файле со списком ОВД-Инфо нет колонок "
            f"{', '.join(missing)}. Схема изменилась — даты не тронуты."
        )

    dates: dict[tuple[str, date], date] = {}
    names: set[str] = set()
    without_date = 0
    for row in table.select(list(_REQUIRED_COLUMNS)).to_pylist():
        name = normalize_name(str(row["name"]))
        names.add(name)
        added = _as_date(row["added_date"])
        if added is None:
            without_date += 1
            continue
        birth = _as_date(row["birth_date"])
        key = (name, birth or date.min)
        # A duplicate key means two of their rows agree on the pair; both carry a day,
        # and the earlier is the one that describes when the entry appeared.
        if key not in dates or added < dates[key]:
            dates[key] = added

    logger.info(
        "event=rfm_inclusion_dates_read rows=%d dated=%d without_date=%d",
        table.num_rows,
        len(dates),
        without_date,
    )
    return InclusionDates(
        by_name_and_birth=dates,
        total_rows=table.num_rows,
        rows_without_added_date=without_date,
        names_in_source=frozenset(names),
    )


def download_inclusion_dates(
    http_client: httpx.Client,
    page_url: str = PUBLISHED_PAGE_URL,
) -> InclusionDates:
    """The page, then the file it points at, then the dates in it.

    Two requests, because the file's name is a content hash that changes on every
    rebuild: there is no address to keep. A `200` that is not the list raises inside
    `read_inclusion_dates` rather than writing nothing over the dates we have.
    """
    page = http_client.get(page_url).text
    file_url = file_url_from_page(page)
    raw = http_client.get(file_url).content
    return read_inclusion_dates(raw)


def _as_date(value: Any) -> date | None:
    """A parquet timestamp as a plain day.

    A day, not an instant: the list publishes a day, and turning it into a moment would
    put a time of day on a record that has none.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(UTC).date() if value.tzinfo else value.date()
    if isinstance(value, date):
        return value
    return None


def match_inclusion_date(
    entry_full_name: str,
    entry_birth_date: date | None,
    dates: InclusionDates,
) -> date | None:
    """The day to write on our entry, or `None` when we cannot establish one.

    `None` is the honest answer in every uncertain case and is not a failure: a name with
    no birth date on either side, a birth date we hold that their file does not, a name
    they do not publish at all. In particular a name that matches on its own gets nothing,
    because a namesake would be given the other person's day without anything saying so.
    """
    if entry_birth_date is None:
        return None
    return dates.by_name_and_birth.get((normalize_name(entry_full_name), entry_birth_date))
