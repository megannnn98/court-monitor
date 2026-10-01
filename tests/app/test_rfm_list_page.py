"""«Перечень»: the list itself, the period filter, and the file that goes with it.

Ирина works from files, so the page and the export have to agree — the same period, the
same rows — or the filter is a thing only the site can do.

The page is also the place where a date is most likely to be read as a statement about
people rather than about records, so the wording is checked here as well.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
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
        session.flush()
        session.add(
            EntityGroupRfMatchRecord(
                group_id=entity.id, entry_id=ids["ИВАНОВ ИВАН ИВАНОВИЧ"], level="full"
            )
        )


def _file_names(text: str) -> list[str]:
    """The names the file holds: the rows, without the header and without the two lines
    of attribution the file ends on."""
    listed = list(csv.reader(io.StringIO(text.lstrip("﻿"))))
    return [
        row[0]
        for row in listed[1:]
        if row and row[0] and not row[0].startswith(("Дата включения", "Дата описывает"))
    ]


def _rows(text: str) -> list[str]:
    """The names of the table's rows, in the order the page shows them."""
    import re

    return re.findall(r"<tr><td>([А-ЯЁ][^<]*)</td><td>", text)


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
        response = client.get("/ui/rfm/export.csv?date_from=2026-09-19&date_to=2026-09-27")

    assert response.status_code == 200
    assert _file_names(response.text) == _rows(page)


def test_the_file_holds_every_row_of_the_period_and_not_only_the_page(
    session_factory: sessionmaker[Session],
) -> None:
    _seed(session_factory)

    with _client(session_factory) as client:
        response = client.get("/ui/rfm/export.csv")

    assert set(ENTRIES) <= set(_file_names(response.text))


def test_the_file_says_whose_day_the_date_is(
    session_factory: sessionmaker[Session],
) -> None:
    """The file leaves the site. Whatever the page says about the date has to be in the
    file, or the rule stops existing the moment she opens it."""
    _seed(session_factory)

    with _client(session_factory) as client:
        response = client.get("/ui/rfm/export.csv")

    text = _flat(response.text)
    assert "ОВД-Инфо" in text
    assert "repression.net/rosfinmonitoring" in text
    assert "не подтверждает" in text, (
        "the days travel with the words about what they are; a bare date column says "
        "«this person entered the list that day»"
    )


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


def test_the_file_is_named_after_the_period_it_holds() -> None:
    """A file in a folder says what it is without being opened."""
    assert _export_name(ListFilters(), TODAY) == "perechen_vse.csv"
    named = _export_name(ListFilters(date_from=date(2026, 9, 1), date_to=date(2026, 9, 30)), TODAY)
    assert named == "perechen_2026-09-01_2026-09-30.csv"


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
