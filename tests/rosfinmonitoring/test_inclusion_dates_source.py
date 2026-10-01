"""Reading the day of inclusion out of the ОВД-Инфо copy of the перечень.

The file is a build artefact of someone else's site, not a documented API, so most of
what is checked here is what happens when it stops looking the way it did: a renamed
column has to stop the read, not be read as something it is not.
"""

from __future__ import annotations

import io
from datetime import UTC, date, datetime

import httpx
import pytest

from rosfinmonitoring.inclusion_dates import (
    InclusionDatesUnavailable,
    download_inclusion_dates,
    file_url_from_page,
    match_inclusion_date,
    read_inclusion_dates,
)

PAGE = """
<script type="module">
registerFile("./assets/cofo-sans.woff2", {"name":"./assets/cofo-sans.woff2"});
registerFile("./data/rfm.parquet", {"name":"./data/rfm.parquet","path":"./_file/data/rfm.ecb72e4c.parquet","lastModified":1790795393266,"size":1553689});
</script>
"""


def _parquet(rows: list[dict[str, object]]) -> bytes:
    import pyarrow
    from pyarrow import parquet

    names = list(rows[0])
    table = pyarrow.table({name: [row.get(name) for row in rows] for name in names})
    sink = io.BytesIO()
    parquet.write_table(table, sink)
    return sink.getvalue()


def _rows() -> list[dict[str, object]]:
    return [
        {
            "name": "ИВАНОВ ИВАН ИВАНОВИЧ",
            "birth_date": datetime(1980, 5, 1, tzinfo=UTC),
            "added_date": datetime(2024, 3, 14, tzinfo=UTC),
        },
        {
            "name": "ПЕТРОВ ПЁТР ПЕТРОВИЧ",
            "birth_date": datetime(1970, 1, 2, tzinfo=UTC),
            "added_date": datetime(2025, 7, 1, tzinfo=UTC),
        },
    ]


def test_the_file_address_is_read_out_of_the_page() -> None:
    assert file_url_from_page(PAGE) == "https://repression.net/_file/data/rfm.ecb72e4c.parquet"


def test_a_page_that_no_longer_points_at_the_file_stops_the_read() -> None:
    """The file's name is a content hash, so the page is the only place it is written
    down. A redesign that drops the reference has to fail loudly: guessing an address
    would read something else and call it the list."""
    with pytest.raises(InclusionDatesUnavailable, match="не ссылается"):
        file_url_from_page("<html><body>перечень</body></html>")


def test_a_file_that_is_not_parquet_is_refused() -> None:
    with pytest.raises(InclusionDatesUnavailable, match="не parquet"):
        read_inclusion_dates(b"<!DOCTYPE html><html>404</html>")


def test_a_renamed_column_stops_the_read_rather_than_being_read_as_something_else() -> None:
    """The one thing this module must never do is read a column whose name has changed
    into a field that looks close enough. A file that no longer has `added_date` has no
    day of inclusion in it, whatever else it has."""
    raw = _parquet(
        [
            {
                "name": "ИВАНОВ ИВАН ИВАНОВИЧ",
                "birth_date": datetime(1980, 5, 1, tzinfo=UTC),
                "included_at": datetime(2024, 3, 14, tzinfo=UTC),
            }
        ]
    )

    with pytest.raises(InclusionDatesUnavailable, match="added_date"):
        read_inclusion_dates(raw)


def test_a_day_is_read_and_the_names_are_normalised_the_way_ours_are() -> None:
    dates = read_inclusion_dates(_parquet(_rows()))

    matched = match_inclusion_date("ИВАНОВ ИВАН ИВАНОВИЧ", date(1980, 5, 1), dates)
    assert matched == date(2024, 3, 14)
    assert match_inclusion_date("иванов  иван иванович", date(1980, 5, 1), dates) == date(
        2024, 3, 14
    ), "ё, лишние пробелы and case are the same person as far as the list is concerned"
    assert match_inclusion_date("ПЕТРОВ ПЕТР ПЕТРОВИЧ", date(1970, 1, 2), dates) == date(2025, 7, 1)


def test_a_day_is_a_day_and_not_a_moment() -> None:
    dates = read_inclusion_dates(_parquet(_rows()))

    matched = match_inclusion_date("ИВАНОВ ИВАН ИВАНОВИЧ", date(1980, 5, 1), dates)

    assert isinstance(matched, date)
    assert not isinstance(matched, datetime), "the list publishes a day, not an instant"


def test_a_name_alone_gets_nothing() -> None:
    """The entry would be a namesake. Writing the day we saw on the list written on our
    entry would put one person's inclusion on another's record, and nothing would say so.
    """
    dates = read_inclusion_dates(_parquet(_rows()))

    assert match_inclusion_date("ИВАНОВ ИВАН ИВАНОВИЧ", None, dates) is None


def test_a_name_with_another_birth_date_gets_nothing() -> None:
    """A namesake, the other way round: same name, someone else's day of birth."""
    dates = read_inclusion_dates(_parquet(_rows()))

    assert match_inclusion_date("ИВАНОВ ИВАН ИВАНОВИЧ", date(1980, 5, 2), dates) is None


def test_a_name_the_list_does_not_publish_gets_nothing() -> None:
    dates = read_inclusion_dates(_parquet(_rows()))

    assert match_inclusion_date("СИДОРОВ СИДОР СИДОРОВИЧ", date(1990, 1, 1), dates) is None


def test_a_day_and_a_month_their_way_round_gets_nothing() -> None:
    """124 entries differ from ours by exactly this: 06.11 against 11.06. It looks like
    one person written twice, and it might be — but confirming it is a decision about a
    person, and the list's own record does not make it. Nothing is written, and the case
    is counted separately instead.
    """
    dates = read_inclusion_dates(_parquet(_rows()))

    assert match_inclusion_date("ИВАНОВ ИВАН ИВАНОВИЧ", date(1980, 11, 5), dates) is None


def test_an_entry_the_list_left_without_a_day_is_not_given_one() -> None:
    raw = _parquet(
        [
            {
                "name": "ИВАНОВ ИВАН ИВАНОВИЧ",
                "birth_date": datetime(1980, 5, 1, tzinfo=UTC),
                "added_date": None,
            }
        ]
    )

    dates = read_inclusion_dates(raw)

    assert match_inclusion_date("ИВАНОВ ИВАН ИВАНОВИЧ", date(1980, 5, 1), dates) is None
    assert dates.rows_without_added_date == 1


def test_when_two_of_their_rows_agree_on_the_person_the_earlier_day_is_their_entry() -> None:
    """A person can be listed, removed and listed again. The first day is the day the
    entry appeared; the second is a re-adding and saying so would date our entry to a
    later day than the one an operator reading «включён» expects."""
    raw = _parquet(
        [
            {
                "name": "ИВАНОВ ИВАН ИВАНОВИЧ",
                "birth_date": datetime(1980, 5, 1, tzinfo=UTC),
                "added_date": datetime(2024, 3, 14, tzinfo=UTC),
            },
            {
                "name": "ИВАНОВ ИВАН ИВАНОВИЧ",
                "birth_date": datetime(1980, 5, 1, tzinfo=UTC),
                "added_date": datetime(2025, 9, 1, tzinfo=UTC),
            },
        ]
    )

    dates = read_inclusion_dates(raw)

    assert match_inclusion_date("ИВАНОВ ИВАН ИВАНОВИЧ", date(1980, 5, 1), dates) == date(
        2024, 3, 14
    )


def test_the_download_reads_the_page_and_then_the_file_it_names() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.path == "/rosfinmonitoring":
            return httpx.Response(200, text=PAGE)
        return httpx.Response(200, content=_parquet(_rows()))

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        dates = download_inclusion_dates(client)

    assert seen == [
        "https://repression.net/rosfinmonitoring",
        "https://repression.net/_file/data/rfm.ecb72e4c.parquet",
    ]
    assert match_inclusion_date("ИВАНОВ ИВАН ИВАНОВИЧ", date(1980, 5, 1), dates) == date(
        2024, 3, 14
    )
