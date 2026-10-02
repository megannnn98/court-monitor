"""The day of inclusion, where an operator meets it: «Результат», Excel, «Расследование».

One rule runs through all of it. The date says when an **entry** of the перечень
appeared; it was matched to the person by name and birth date, and that is the strongest
thing we can say. So the wording is about the entry, the date is never attached to a
namesake, and the attribution ОВД-Инфо travels with the number because their list is
CC BY 3.0.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from io import BytesIO
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook
from sqlalchemy.orm import Session, sessionmaker

from db.orm_models import (
    EntityGroupPoliticsRecord,
    EntityGroupRecord,
    EntityGroupRfMatchRecord,
    RosfinmonitoringEntryRecord,
    RosfinmonitoringSnapshotRecord,
)
from entities.rf_entry import entry_included_text
from web.app import app
from web.dependencies import get_db
from web.ui.political import political_xlsx
from web.ui.political_rows import named_row

INCLUDED = datetime(2024, 3, 14, tzinfo=UTC)


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


def _seed(session_factory: sessionmaker[Session], *, namesake: bool = True) -> None:
    """Анна Смирнова on the list with her patronymic, and Иван Иванов on it without one."""
    now = datetime.now(UTC)
    with session_factory.begin() as session:
        snapshot = RosfinmonitoringSnapshotRecord(
            snapshot_date=now,
            source_url="https://example.test",
            content_hash="x",
            entry_count=2,
            fetched_at=now,
        )
        session.add(snapshot)
        session.flush()
        confirmed = RosfinmonitoringEntryRecord(
            snapshot_id=snapshot.id,
            full_name="СМИРНОВА АННА ПЕТРОВНА",
            normalized_name="смирнова анна петровна",
            matching_key="смирновааннапетровна",
            birth_date=datetime(1990, 2, 1, tzinfo=UTC),
            birth_place="Г. МОСКВА",
            inclusion_date=INCLUDED,
        )
        session.add(confirmed)
        entries: dict[str, RosfinmonitoringEntryRecord] = {"анна смирнова": confirmed}
        if namesake:
            # The same person listed under another name, matched by name and surname only.
            maybe = RosfinmonitoringEntryRecord(
                snapshot_id=snapshot.id,
                full_name="ИВАНОВ ИВАН ИВАНОВИЧ",
                normalized_name="иванов иван иванович",
                matching_key="ивановиваниванович",
                inclusion_date=datetime(2019, 5, 2, tzinfo=UTC),
            )
            session.add(maybe)
            entries["иван иванов"] = maybe
        session.flush()
        for key, name, days in (
            ("анна смирнова", "Анна Смирнова", 10),
            *(() if not namesake else (("иван иванов", "Иван Иванов", 400),)),
        ):
            entity = EntityGroupRecord(
                key=key,
                name=name,
                variants=[[name, 1]],
                mention_count=1,
                article_count=1,
                event_types={},
                regions=[["Москва", 1]],
                last_published_at=now - timedelta(days=days),
            )
            session.add(entity)
            session.flush()
            session.add(
                EntityGroupPoliticsRecord(
                    group_id=entity.id,
                    verdict="political",
                    method="model",
                    reason=f"так про {name}",
                    quote="цитата",
                )
            )
            session.add(
                EntityGroupRfMatchRecord(
                    group_id=entity.id,
                    entry_id=entries[key].id,
                    level="full" if key == "анна смирнова" else "name",
                )
            )


def test_the_date_is_worded_as_the_entry_s_in_and_never_the_person_s() -> None:
    assert entry_included_text(INCLUDED) == "запись перечня включена 14.03.2024"


def test_the_result_page_shows_the_date_of_the_matched_entry(
    session_factory: sessionmaker[Session],
) -> None:
    _seed(session_factory)

    with _client(session_factory) as client:
        page = client.get("/ui/political?known=all").text

    assert "запись перечня включена 14.03.2024" in page
    assert "в перечне РФМ" in page


def test_a_namesake_is_shown_no_date(
    session_factory: sessionmaker[Session],
) -> None:
    """Иван Иванов matched on the name and surname alone. The entry's day is 02.05.2019
    and it belongs to whoever that entry is about, which the comparison never established
    — so it is not on the page at all."""
    _seed(session_factory)

    with _client(session_factory) as client:
        page = client.get("/ui/political?known=all").text

    assert "возможно в перечне" in page
    assert "02.05.2019" not in page, (
        "the day belongs to the namesake's entry, not to the person in the row"
    )


def test_the_excel_shows_a_namesake_no_date(
    session_factory: sessionmaker[Session],
) -> None:
    """The same rule in the file, which is where Ирина actually works. The page and the
    export are two wordings of one thing, and the export is not the one that may drift."""
    _seed(session_factory)

    with _client(session_factory) as client:
        response = client.get("/ui/political/export.xlsx?known=all")

    workbook = load_workbook(BytesIO(response.content))
    rows = [
        str(cell.value)
        for row in workbook["Результат"].iter_rows()
        for cell in row
        if cell.value is not None
    ]
    assert any("возможно тёзка" in cell for cell in rows)
    assert not any("02.05.2019" in cell for cell in rows)
    assert any("запись перечня включена 14.03.2024" in cell for cell in rows), (
        "the one entry we did identify keeps its day"
    )


def test_the_result_page_carries_the_attribution(
    session_factory: sessionmaker[Session],
) -> None:
    """Ирина has no access to the site and works from files; wherever the number is
    shown, the source it came from is named, because their list is CC BY 3.0."""
    _seed(session_factory)

    with _client(session_factory) as client:
        page = client.get("/ui/political?known=all").text

    assert "ОВД-Инфо" in page
    assert "repression.net/rosfinmonitoring" in page


def test_the_excel_carries_the_date_and_the_attribution(
    session_factory: sessionmaker[Session],
) -> None:
    _seed(session_factory)

    with _client(session_factory) as client:
        response = client.get("/ui/political/export.xlsx?known=all")

    assert response.status_code == 200
    workbook = load_workbook(BytesIO(response.content))
    cells = [
        str(cell.value)
        for sheet in workbook
        for row in sheet.iter_rows()
        for cell in row
        if cell.value is not None
    ]
    assert any("запись перечня включена 14.03.2024" in cell for cell in cells)
    assert any("ОВД-Инфо" in cell for cell in cells), (
        "the attribution has to travel with the dates, not sit on a page nobody opens"
    )


def test_the_excel_says_the_date_is_the_entry_s(
    session_factory: sessionmaker[Session],
) -> None:
    _seed(session_factory)

    with _client(session_factory) as client:
        response = client.get("/ui/political/export.xlsx?known=all")

    workbook = load_workbook(BytesIO(response.content))
    notes = " ".join(
        str(cell.value)
        for sheet in workbook
        for row in sheet.iter_rows()
        for cell in row
        if cell.value is not None
    )
    assert "не подтверждает" in notes, (
        "the file says in words that a name and a date is not an identification"
    )


def test_the_dossier_shows_the_date_of_the_entry(
    session_factory: sessionmaker[Session],
) -> None:
    _seed(session_factory, namesake=False)

    with _client(session_factory) as client:
        page = client.get(f"/ui/investigations/{quote('анна смирнова')}").text

    assert "запись перечня включена 14.03.2024" in page
    assert "ОВД-Инфо" in page


def test_the_dossier_shows_a_namesake_no_date(
    session_factory: sessionmaker[Session],
) -> None:
    """Иван Иванов matched on the name and surname alone. His entry carries 02.05.2019 and
    that day belongs to whoever the entry is about, which the comparison never
    established — so the dossier does not put it next to his name either."""
    _seed(session_factory)

    with _client(session_factory) as client:
        page = client.get(f"/ui/investigations/{quote('иван иванов')}").text

    assert "возможно в перечне" in page
    assert "02.05.2019" not in page


def test_the_dossier_does_not_say_the_person_has_been_on_the_list_since(
    session_factory: sessionmaker[Session],
) -> None:
    """The phrasing that would turn the entry's day into a fact about the person. The
    page must not say it, in any wording."""
    _seed(session_factory, namesake=False)

    with _client(session_factory) as client:
        page = client.get(f"/ui/investigations/{quote('анна смирнова')}").text

    assert not re.search(r"в перечне[^<]{0,20}\bс\s+14\.03\.2024", page), (
        "«в перечне с …» says the person entered the list that day; the date is the entry's"
    )


def test_an_entry_without_a_date_shows_the_entry_and_nothing_more(
    session_factory: sessionmaker[Session],
) -> None:
    """165 of ОВД-Инфо's rows carry no birth date, so no date can be established. The
    person is still on the list — the page says so, and says nothing about when."""
    now = datetime.now(UTC)
    with session_factory.begin() as session:
        snapshot = RosfinmonitoringSnapshotRecord(
            snapshot_date=now,
            source_url="https://example.test",
            content_hash="y",
            entry_count=1,
            fetched_at=now,
        )
        session.add(snapshot)
        session.flush()
        entry = RosfinmonitoringEntryRecord(
            snapshot_id=snapshot.id,
            full_name="СМИРНОВА АННА ПЕТРОВНА",
            normalized_name="смирнова анна петровна",
            matching_key="смирновааннапетровна",
        )
        session.add(entry)
        entity = EntityGroupRecord(
            key="анна смирнова",
            name="Анна Смирнова",
            variants=[["Анна Смирнова", 1]],
            mention_count=1,
            article_count=1,
            event_types={},
            regions=[["Москва", 1]],
            last_published_at=now,
        )
        session.add(entity)
        session.flush()
        session.add(
            EntityGroupPoliticsRecord(
                group_id=entity.id,
                verdict="political",
                method="model",
                reason="так про неё",
                quote="цитата",
            )
        )
        session.add(EntityGroupRfMatchRecord(group_id=entity.id, entry_id=entry.id, level="full"))

    with _client(session_factory) as client:
        page = client.get("/ui/political?known=all").text

    assert "в перечне РФМ" in page
    assert not re.search(r"запись перечня включена \d", page), (
        "the legend explains the wording in words; no row of this list has a day to show"
    )


@pytest.mark.parametrize(
    ("inclusion_date", "expected"),
    [(None, ""), (INCLUDED, "запись перечня включена 14.03.2024")],
)
def test_the_wording_depends_only_on_whether_there_is_a_date(
    inclusion_date: datetime | None, expected: str
) -> None:
    assert entry_included_text(inclusion_date) == expected


def test_the_xlsx_of_rows_without_matches_says_nothing_about_dates() -> None:
    """A row with no list entry has no entry to date; the column must be empty rather
    than carrying a stray word."""
    from db.orm_models import EntityGroupRecord as Record
    from entities.politics import POLITICAL

    entity = Record(id=1, key="k", name="Анна Смирнова", variants=[], mention_count=1, regions=[])
    politics = EntityGroupPoliticsRecord(
        group_id=1, verdict=POLITICAL, method="model", reason="р", quote="ц"
    )
    workbook = load_workbook(BytesIO(political_xlsx([named_row(entity, politics)])))
    rows = [
        str(cell.value)
        for row in workbook["Результат"].iter_rows()
        for cell in row
        if cell.value is not None
    ]
    assert not any("запись перечня" in cell for cell in rows)
    assert not any("14.03.2024" in cell for cell in rows)
