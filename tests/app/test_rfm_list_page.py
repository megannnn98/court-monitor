"""«Перечень»: the list itself, the period filter, and the file that goes with it.

Ирина works from files, so the page and the export have to agree — the same period, the
same rows — or the filter is a thing only the site can do.

The page is also the place where a date is most likely to be read as a statement about
people rather than about records, so the wording is checked here as well.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook
from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from db.orm_models import (
    EntityGroupRecord,
    EntityGroupRfMatchRecord,
    RosfinmonitoringEntryRecord,
    RosfinmonitoringSnapshotRecord,
)
from web.app import app
from web.dependencies import get_db
from web.ui.rfm_list import ListFilters, _export_name, _period

NOW = datetime.now(UTC)
TODAY = NOW.date()

# One entry per case, so a filter that is off by a day or inclusive/exclusive wrong shows.
ENTRIES = {
    "ИВАНОВ ИВАН ИВАНОВИЧ": date(2026, 9, 28),
    "ПЕТРОВ ПЕТР ПЕТРОВИЧ": date(2026, 9, 20),
    "СИДОРОВ СИДОР СИДОРОВИЧ": date(2025, 3, 14),
    "СЕМЁНОВ СЕМЁН СЕМЁНОВИЧ": date(2022, 1, 5),
}


@contextmanager
def _client(session_factory: sessionmaker[Session]) -> Iterator[TestClient]:
    def override_get_db() -> Iterator[Session]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)


def _seed(session_factory: sessionmaker[Session], *, undated: bool = True) -> None:
    with session_factory.begin() as session:
        snapshot = RosfinmonitoringSnapshotRecord(
            snapshot_date=NOW,
            source_url="https://www.fedsfm.ru/documents/terrorists-catalog-portal-act",
            content_hash="published",
            entry_count=len(ENTRIES) + 1,
            fetched_at=NOW,
        )
        session.add(snapshot)
        session.flush()
        ids: dict[str, int] = {}
        for name, included in ENTRIES.items():
            entry = RosfinmonitoringEntryRecord(
                snapshot_id=snapshot.id,
                full_name=name,
                normalized_name=name.lower(),
                matching_key=name.lower().replace(" ", ""),
                birth_date=datetime(1980, 5, 1, tzinfo=UTC),
                birth_place="Г. МОСКВА",
                inclusion_date=datetime(included.year, included.month, included.day, tzinfo=UTC),
            )
            session.add(entry)
            session.flush()
            ids[name] = entry.id
        if undated:
            # 165 of ОВД-Инфо's rows carry no birth date, so no date can be established.
            # Such an entry belongs on the page — it is in the list — without a day.
            session.add(
                RosfinmonitoringEntryRecord(
                    snapshot_id=snapshot.id,
                    full_name="КУДРОВ КУДР КУДРОВИЧ",
                    normalized_name="кудров кудр кудрович",
                    matching_key="кудровкудркудрович",
                )
            )
        entity = EntityGroupRecord(
            key="иван иванов",
            name="Иван Иванов",
            variants=[["Иван Иванов", 1]],
            mention_count=1,
            article_count=1,
            event_types={},
            regions=[],
        )
        session.add(entity)
        maybe = EntityGroupRecord(
            key="пётр сидоров",
            name="Пётр Сидоров",
            variants=[["Пётр Сидоров", 1]],
            mention_count=1,
            article_count=1,
            event_types={},
            regions=[],
        )
        session.add(maybe)
        session.flush()
        # One entry we identified, one we only half did: the second matched on the name
        # and surname alone, so it may be somebody else's record.
        session.add(
            EntityGroupRfMatchRecord(
                group_id=entity.id, entry_id=ids["ИВАНОВ ИВАН ИВАНОВИЧ"], level="full"
            )
        )
        session.add(
            EntityGroupRfMatchRecord(
                group_id=maybe.id, entry_id=ids["СИДОРОВ СИДОР СИДОРОВИЧ"], level="name"
            )
        )


def _file_names(content: bytes) -> list[str]:
    """The names the file holds, read from the sheet the operator will actually open."""
    sheet = load_workbook(BytesIO(content))["Перечень"]
    return [str(row[0].value) for row in sheet.iter_rows(min_row=2) if row[0].value is not None]


def _rows(text: str) -> list[str]:
    """The names of the table's rows, in the order the page shows them."""
    import re

    return re.findall(r"<tr><td>([А-ЯЁ][^<]*?) <button", text)


def _flat(text: str) -> str:
    """The page's words with the line breaks the markup wraps them at removed.

    The source is wrapped to fit the line length; what an operator reads is one
    sentence, and a test must not turn on where the wrap fell.
    """
    return " ".join(text.split())


def test_the_page_shows_the_entries_newest_first(
    session_factory: sessionmaker[Session],
) -> None:
    _seed(session_factory)

    with _client(session_factory) as client:
        page = client.get("/ui/rfm").text

    assert _rows(page)[:4] == [
        "ИВАНОВ ИВАН ИВАНОВИЧ",
        "ПЕТРОВ ПЕТР ПЕТРОВИЧ",
        "СИДОРОВ СИДОР СИДОРОВИЧ",
        "СЕМЁНОВ СЕМЁН СЕМЁНОВИЧ",
    ]
    assert "28.09.2026" in page
    assert "05.01.2022" in page


def test_the_period_shows_only_what_was_added_in_it(
    session_factory: sessionmaker[Session],
) -> None:
    _seed(session_factory)

    with _client(session_factory) as client:
        page = client.get("/ui/rfm?date_from=2026-09-19&date_to=2026-09-27").text

    assert _rows(page) == ["ПЕТРОВ ПЕТР ПЕТРОВИЧ"]


def test_the_period_includes_both_ends(
    session_factory: sessionmaker[Session],
) -> None:
    """A filter «с 20.09 по 30.09» that quietly drops the 20th is a filter nobody trusts:
    an operator looking for a person they were told about would miss them."""
    _seed(session_factory)

    with _client(session_factory) as client:
        page = client.get("/ui/rfm?date_from=2026-09-20&date_to=2026-09-28").text

    assert _rows(page) == ["ИВАНОВ ИВАН ИВАНОВИЧ", "ПЕТРОВ ПЕТР ПЕТРОВИЧ"]


def test_the_period_button_means_the_same_as_the_dates(
    session_factory: sessionmaker[Session],
) -> None:
    """«30 дней» and the two dates typed by hand must give one answer, or the operator
    cannot tell which of the two they are looking at."""
    _seed(session_factory)
    since = (TODAY - timedelta(days=30)).isoformat()

    with _client(session_factory) as client:
        by_button = client.get("/ui/rfm?days=30").text
        by_dates = client.get(f"/ui/rfm?date_from={since}&date_to={TODAY.isoformat()}").text

    assert _rows(by_button) == _rows(by_dates)


def test_the_file_holds_the_period_the_page_shows(
    session_factory: sessionmaker[Session],
) -> None:
    """Ирина has no access to the site. The file is the deliverable, and it must be the
    filtered list, not the whole list."""
    _seed(session_factory)

    with _client(session_factory) as client:
        page = client.get("/ui/rfm?date_from=2026-09-19&date_to=2026-09-27").text
        response = client.get("/ui/rfm/export.xlsx?date_from=2026-09-19&date_to=2026-09-27")

    assert response.status_code == 200
    assert _file_names(response.content) == _rows(page)


def test_the_file_holds_every_row_of_the_period_and_not_only_the_page(
    session_factory: sessionmaker[Session],
) -> None:
    _seed(session_factory)

    with _client(session_factory) as client:
        response = client.get("/ui/rfm/export.xlsx")

    assert set(ENTRIES) <= set(_file_names(response.content))


def test_the_file_says_whose_day_the_date_is(
    session_factory: sessionmaker[Session],
) -> None:
    """The file leaves the site. Whatever the page says about the date has to be in the
    file, or the rule stops existing the moment she opens it."""
    _seed(session_factory)

    with _client(session_factory) as client:
        response = client.get("/ui/rfm/export.xlsx")

    notes = _flat(
        " ".join(
            str(cell.value)
            for row in load_workbook(BytesIO(response.content))["Источник дат"].iter_rows()
            for cell in row
            if cell.value is not None
        )
    )
    assert "ОВД-Инфо" in notes
    assert "repression.net/rosfinmonitoring" in notes
    assert "не подтверждает" in notes, (
        "the days travel with the words about what they are; a bare date column says "
        "«this person entered the list that day»"
    )


def test_the_file_is_a_real_excel_book(
    session_factory: sessionmaker[Session],
) -> None:
    """A CSV opens in Russian Excel as one column, and the operator works from files
    rather than from the site — so the file is the deliverable, and a deliverable that
    opens wrongly is not one. Checked as a file, not as a string: the point is that
    Excel can read it, and a CSV that happens to contain the right words cannot be."""
    _seed(session_factory)

    with _client(session_factory) as client:
        response = client.get("/ui/rfm/export.xlsx")

    assert response.content[:2] == b"PK", "an xlsx is a zip, a csv is not"
    assert response.headers["content-type"].endswith("spreadsheetml.sheet")
    assert "xlsx" in response.headers["content-disposition"]
    workbook = load_workbook(BytesIO(response.content))
    sheet = workbook["Перечень"]
    assert [cell.value for cell in sheet[1]][:5] == [
        "ФИО в перечне",
        "Дата рождения",
        "Место рождения",
        "Запись включена",
        "Наше совпадение",
    ]


def test_the_dates_in_the_file_are_dates_and_not_text(
    session_factory: sessionmaker[Session],
) -> None:
    """A date written as text cannot be sorted or counted in the file, which is the one
    thing an operator does with a period they were given."""
    import datetime as dt

    _seed(session_factory)

    with _client(session_factory) as client:
        response = client.get("/ui/rfm/export.xlsx")

    sheet = load_workbook(BytesIO(response.content))["Перечень"]
    rows = list(sheet.iter_rows(min_row=2))
    included = [row for row in rows if row[3].value is not None]
    assert included, "the period has entries with a day"
    for row in included:
        assert isinstance(row[3].value, dt.datetime | dt.date), (
            "the day of inclusion must arrive as a date, or the operator cannot sort by it"
        )
        assert sheet.cell(row=row[0].row, column=4).number_format == "DD.MM.YYYY"


def test_the_page_says_the_day_is_the_entry_s(
    session_factory: sessionmaker[Session],
) -> None:
    _seed(session_factory)

    with _client(session_factory) as client:
        page = client.get("/ui/rfm").text

    assert "свойство записи перечня, а не человека" in _flat(page)
    assert "не подтверждает" in _flat(page)


def test_an_entry_without_a_day_is_listed_without_one(
    session_factory: sessionmaker[Session],
) -> None:
    _seed(session_factory)

    with _client(session_factory) as client:
        page = client.get("/ui/rfm?date_from=2000-01-01&date_to=2030-01-01").text

    assert "КУДРОВ КУДР КУДРОВИЧ" not in _rows(page), (
        "without a day it cannot be in a period; it stays off the list until it has one"
    )
    with _client(session_factory) as client:
        undated = client.get("/ui/rfm").text
    assert "КУДРОВ КУДР КУДРОВИЧ" not in _rows(undated)


def test_a_period_that_covers_nothing_says_so_instead_of_looking_empty(
    session_factory: sessionmaker[Session],
) -> None:
    _seed(session_factory)

    with _client(session_factory) as client:
        page = client.get("/ui/rfm?date_from=2019-01-01&date_to=2019-12-31").text

    assert "Записей нет" in page
    assert "За выбранный период записей нет" in page


def test_our_own_match_is_a_link_and_not_a_verdict(
    session_factory: sessionmaker[Session],
) -> None:
    """The page says which of our cases a list entry was matched to, and says it as a
    link the operator can open — not as a claim that the person is the one in the news."""
    _seed(session_factory)

    with _client(session_factory) as client:
        page = client.get("/ui/rfm").text

    assert "Наше совпадение" in page
    assert "Иван Иванов" in page
    assert "/ui/investigations/" in page


def test_a_namesake_is_marked_as_one_and_a_full_match_is_not(
    session_factory: sessionmaker[Session],
) -> None:
    """The link looks the same either way, so the distinction has to be in the words.

    ИВАНОВ was matched with his patronymic and is who he is; СИДОРОВ was matched on the
    name and surname alone and may be a namesake — a different person, whose entry's day
    of inclusion says nothing about them. Reading both as one link would put a case on
    the wrong record.
    """
    _seed(session_factory)

    with _client(session_factory) as client:
        page = _flat(client.get("/ui/rfm").text)

    assert "в перечне" in page, "the entry we identified says so"
    assert "возможно тёзка" in page, (
        "the entry matched on a name alone must not read like the one we identified"
    )


def test_where_one_entry_matched_two_people_the_identified_one_is_shown(
    session_factory: sessionmaker[Session],
) -> None:
    """One list entry, two of our cases matched to it — one on the whole name, one on the
    name alone. The one we identified has to be the one on the page.

    The two levels are the words «full» and «name», and sorting them descending puts
    «name» first: alphabetically it is the larger. An earlier version did that, showed
    the namesake, and the comment above it claimed the opposite.
    """
    _seed(session_factory)
    with session_factory.begin() as session:
        entry_id = session.execute(
            select(RosfinmonitoringEntryRecord.id).where(
                RosfinmonitoringEntryRecord.full_name == "ИВАНОВ ИВАН ИВАНОВИЧ"
            )
        ).scalar_one()
        namesake = EntityGroupRecord(
            key="иван иванов тёзка",
            name="Иван Иванов (тёзка)",
            variants=[["Иван Иванов (тёзка)", 1]],
            mention_count=1,
            article_count=1,
            event_types={},
            regions=[],
        )
        session.add(namesake)
        session.flush()
        session.add(EntityGroupRfMatchRecord(group_id=namesake.id, entry_id=entry_id, level="name"))

    with _client(session_factory) as client:
        page = _flat(client.get("/ui/rfm").text)

    assert "Иван Иванов (тёзка)" not in page, (
        "a namesake matched on a name alone must not stand for the entry in place of the "
        "person we identified"
    )
    assert "в перечне" in page


def test_the_file_tells_the_two_kinds_of_match_apart_too(
    session_factory: sessionmaker[Session],
) -> None:
    """The file is where the operator works, so the same words have to be in it."""
    _seed(session_factory)

    with _client(session_factory) as client:
        response = client.get("/ui/rfm/export.xlsx")

    sheet = load_workbook(BytesIO(response.content))["Перечень"]
    said = {
        row[0].value: row[5].value for row in sheet.iter_rows(min_row=2) if row[0].value is not None
    }
    assert said["ИВАНОВ ИВАН ИВАНОВИЧ"] == "в перечне"
    assert said["СИДОРОВ СИДОР СИДОРОВИЧ"] == "возможно тёзка"


def test_the_page_reads_a_hundred_rows_and_not_the_whole_list(
    session_factory: sessionmaker[Session],
) -> None:
    """The list is 23 023 rows and the page shows a hundred of them. Reading them all to
    fill in one table is a query the operator waits on for nothing."""
    import datetime as dt

    from db.orm_models import RosfinmonitoringEntryRecord, RosfinmonitoringSnapshotRecord

    _seed(session_factory)
    with session_factory.begin() as session:
        latest = (
            session.query(RosfinmonitoringSnapshotRecord)
            .order_by(RosfinmonitoringSnapshotRecord.id.desc())
            .first()
        )
        assert latest is not None
        session.add_all(
            RosfinmonitoringEntryRecord(
                snapshot_id=latest.id,
                full_name=f"ПРОБНИК {index:05d}",
                normalized_name=f"пробник {index:05d}",
                matching_key=f"пробник{index:05d}",
                inclusion_date=dt.datetime(2026, 9, 1, tzinfo=UTC),
                raw_data={},
            )
            for index in range(500)
        )

    with _client(session_factory) as client:
        page = client.get("/ui/rfm").text

    assert page.count("<tr><td>") == 100, "the page shows a page, not the list"
    assert "ПРОБНИК 00000" in page


def test_the_empty_table_spans_every_column(
    session_factory: sessionmaker[Session],
) -> None:
    """A colspan short by one leaves the «Записей нет» row narrower than the header, and
    the table reads as having a column nobody can fill. A period with nothing in it, so
    the table is still rendered — with its header, and with no rows."""
    import re

    _seed(session_factory)

    with _client(session_factory) as client:
        page = client.get("/ui/rfm?date_from=2019-01-01&date_to=2019-12-31").text

    header = re.search(r"<thead><tr>(.*?)</tr></thead>", page, re.DOTALL)
    assert header is not None
    columns = header.group(1).count("<th>")
    empty = re.search(r'<td colspan="(\d+)"', page)
    assert empty is not None, "an empty table must say so"
    assert int(empty.group(1)) == columns, (
        f"the empty row spans {empty.group(1)} of {columns} columns"
    )


def test_the_file_is_named_after_the_period_it_holds() -> None:
    """A file in a folder says what it is without being opened."""
    assert _export_name(ListFilters(), TODAY) == "perechen_vse.xlsx"
    named = _export_name(ListFilters(date_from=date(2026, 9, 1), date_to=date(2026, 9, 30)), TODAY)
    assert named == "perechen_2026-09-01_2026-09-30.xlsx"


def test_the_period_computation_is_readable_without_the_page() -> None:
    today = date(2026, 10, 1)
    assert _period(ListFilters(), today) == (None, None)
    assert _period(ListFilters(days=7), today) == (date(2026, 9, 24), today)
    assert _period(ListFilters(date_from=date(2026, 9, 1)), today) == (date(2026, 9, 1), None)
    assert _period(ListFilters(days=7, date_from=date(2026, 1, 1)), today) == (
        date(2026, 1, 1),
        None,
    ), "typed dates win over the button, as on «Результате»"


def test_the_page_is_in_the_menu(session_factory: sessionmaker[Session]) -> None:
    with _client(session_factory) as client:
        page = client.get("/ui/political").text
    assert 'href="/ui/rfm"' in page


@pytest.mark.parametrize("text", ["", "не дата", "2026-13-45"])
def test_a_broken_date_in_the_address_is_ignored_not_fatal(
    session_factory: sessionmaker[Session], text: str
) -> None:
    _seed(session_factory)

    with _client(session_factory) as client:
        response = client.get(f"/ui/rfm?date_from={text}")

    assert response.status_code == 200


def test_a_day_of_the_operator_s_table_is_marked_on_the_page_and_in_the_file(
    session_factory: sessionmaker[Session],
) -> None:
    _seed(session_factory)
    with session_factory.begin() as session:
        session.execute(
            text(
                "UPDATE rosfinmonitoring_entries SET inclusion_source = 'operator' "
                "WHERE full_name = 'ПЕТРОВ ПЕТР ПЕТРОВИЧ'"
            )
        )

    with _client(session_factory) as client:
        page = client.get("/ui/rfm").text
        book = client.get("/ui/rfm/export.xlsx").content

    assert page.count('<div class="muted">по таблице оператора</div>') == 1
    petrov = page[page.index("ПЕТРОВ ПЕТР ПЕТРОВИЧ") :]
    assert "по таблице оператора" in petrov[: petrov.index("</tr>")]
    sheet = load_workbook(BytesIO(book))["Перечень"]
    header = [cell.value for cell in sheet[1]]
    source = header.index("Откуда дата")
    by_name = {row[0].value: row[source].value for row in sheet.iter_rows(min_row=2)}
    assert by_name["ПЕТРОВ ПЕТР ПЕТРОВИЧ"] == "по таблице оператора"
    assert by_name["ИВАНОВ ИВАН ИВАНОВИЧ"] is None


def test_those_removed_from_the_list_have_a_page_of_their_own(
    session_factory: sessionmaker[Session],
) -> None:
    from rosfinmonitoring.operator_table import OperatorRow, store

    _seed(session_factory)
    with _client(session_factory) as client:
        assert "Исключены из перечня" not in client.get("/ui/rfm").text

    def row(name: str, *, removed: bool, added: date | None = None) -> OperatorRow:
        return OperatorRow(name, date(1980, 5, 1), "Г. ОМСК", added, removed, "экстремизм", "")

    with session_factory.begin() as session:
        store(
            session,
            [
                row("УШЕДШИЙ ИВАН ИЛЬИЧ", removed=True, added=date(2021, 4, 2)),
                # Twice in the table: one person, one row of the page, the later day.
                row("УШЕДШИЙ ИВАН ИЛЬИЧ", removed=True, added=date(2019, 1, 1)),
                # Removed once, on the list again: the list's entry, not a removed one.
                row("ИВАНОВ ИВАН ИВАНОВИЧ", removed=True, added=date(2020, 1, 1)),
                row("ЛИШНИЙ ОЛЕГ ПЕТРОВИЧ", removed=False),
            ],
        )

    with _client(session_factory) as client:
        page = _flat(client.get("/ui/rfm").text)
        gone = _flat(client.get("/ui/rfm?removed=1").text)

    assert "Исключены из перечня, по таблице оператора: 1." in page
    assert '<a href="/ui/rfm?removed=1">Показать</a>' in page
    assert _rows(gone) == ["УШЕДШИЙ ИВАН ИЛЬИЧ"]
    assert "<td>02.04.2021</td>" in gone and "01.01.2019" not in gone
    assert "в действующем перечне этих людей нет" in gone
