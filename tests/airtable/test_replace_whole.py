"""Replacing a list that is a copy of an Airtable view — and refusing to when it is not one.

The database is full of rows from three earlier ways of naming a row, all of them
`sync`-only: they accumulate and are never revoked, because the sync had no notion of a
row the export no longer names. That is what a list which is a copy of somebody else's
view must not be. So this file is mostly about the guard, because the guard is the whole
risk: delete on the strength of a download that turned out to be short, and 13 769 people
are gone with no way back from the console.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from airtable.client import AirtableRecord
from airtable.links import ShareTableClient
from airtable.replace import (
    MINIMUM_ROWS_TO_TRUST,
    UntrustedExport,
    download_looks_complete,
    removal_is_safe,
)
from db.orm_models import AirtableKnownPersonRecord, CriminalArticleRecord


def _names(session_factory: sessionmaker[Session]) -> list[str]:
    with session_factory() as session:
        return list(
            session.scalars(
                select(AirtableKnownPersonRecord.full_name).order_by(
                    AirtableKnownPersonRecord.full_name
                )
            )
        )


def _sync(session_factory: sessionmaker[Session], people: list[tuple[str, str]]) -> None:
    """Write the list, and refuse the whole export if it is not believable.

    The refusal is an exception on purpose — it is what a refused export looks like from
    inside the transaction — so a test that does not expect one is asserting the happy
    path, and a test that wants one wraps this call in `pytest.raises`.
    """
    from airtable.repository import sync_known_persons

    records = [
        AirtableRecord(f"rec{index}", {"ФИО": name, "Дата рождения": born})
        for index, (name, born) in enumerate(people, start=1)
    ]
    with session_factory.begin() as session:
        sync_known_persons(session, records, replace=True)


def _many(count: int) -> list[tuple[str, str]]:
    return [(f"Человек Номер {n:04d}", "01.01.1980") for n in range(count)]


def test_an_older_generation_of_ids_leaves_when_the_new_export_is_complete(
    session_factory: sessionmaker[Session],
) -> None:
    """The exact state the database got into: rows from three ways of naming a row, all
    of them kept forever, for 13 769 people.

    A complete export of the current generation has to leave exactly the current
    generation behind — not the union, and not the newest run's rows added to the rest.
    """

    with session_factory.begin() as session:
        # Two older generations, each naming the same people differently.
        session.add_all(
            [
                AirtableKnownPersonRecord(
                    external_id=f"share:{n}",
                    full_name=f"Человек Номер {n:04d}",
                    normalized_name=f"человек номер {n:04d}",
                    matching_key=f"человек номер {n:04d}",
                )
                for n in range(1200)
            ]
            + [
                AirtableKnownPersonRecord(
                    external_id=f"share:known_persons:{n}",
                    full_name=f"Человек Номер {n:04d}",
                    normalized_name=f"человек номер {n:04d}",
                    matching_key=f"человек номер {n:04d}",
                )
                for n in range(1200)
            ]
        )
    _sync(session_factory, _many(1200))

    with session_factory() as session:
        ids = set(session.scalars(select(AirtableKnownPersonRecord.external_id)))
        count = len(list(session.scalars(select(AirtableKnownPersonRecord))))
    assert count == 1200
    assert all(i.startswith("rec") for i in ids), ids


def test_nothing_is_removed_when_the_export_is_too_small_to_believe(
    session_factory: sessionmaker[Session],
) -> None:
    """The accident this exists to prevent: a link that answers with a fragment, or a
    view that was narrowed by accident, and a list emptied on the strength of it."""
    _sync(session_factory, _many(1200))
    before = _names(session_factory)

    with pytest.raises(UntrustedExport):
        _sync(session_factory, _many(10))

    assert _names(session_factory) == before


def test_nothing_is_written_either_when_the_export_is_too_small(
    session_factory: sessionmaker[Session],
) -> None:
    """The refusal takes the writes with it.

    An export we have just called untrustworthy is not a smaller list, it is not the
    list. Writing the ten rows it did manage to return would leave the table holding the
    old list *and* those ten — half of an export we said we could not believe.
    """
    _sync(session_factory, _many(1200))
    before = _names(session_factory)

    with pytest.raises(UntrustedExport):
        _sync(session_factory, [("Новый Человек", "01.01.1990"), ("Ещё Один", "02.02.1990")])

    assert _names(session_factory) == before


def test_a_small_list_cannot_be_emptied_through_the_button(
    session_factory: sessionmaker[Session],
) -> None:
    """A link that answers with a view narrowed to nothing, and an operator who really
    did strike every row out of Airtable, look the same from here. The button refuses
    both.

    That is the deliberate choice, and it has a price: emptying a small list is not
    something this button can do. The report says what happened and what to do about it,
    so the refusal is visible rather than silent.
    """
    _sync(session_factory, [("Иван Иванов", "01.01.1980"), ("Ольга Минакова", "02.02.1980")])
    before = _names(session_factory)

    with pytest.raises(UntrustedExport, match="минимум 100"):
        _sync(session_factory, [])

    assert _names(session_factory) == before


def test_the_removal_and_the_write_are_one_piece_of_work(
    session_factory: sessionmaker[Session],
) -> None:
    """A failure in the middle must leave the previous list standing whole, not half
    replaced: the writes and the removals share one transaction."""
    from airtable.repository import sync_known_persons

    _sync(session_factory, _many(1200))
    before = _names(session_factory)

    records = [
        AirtableRecord(f"rec{n}", {"ФИО": f"Человек Номер {n:04d}", "Дата рождения": "01.01.1980"})
        for n in range(1200)
    ]

    def explode(row: object, values: dict[str, object]) -> None:
        raise RuntimeError("сбой в середине записи")

    from airtable import repository

    original = repository._write
    repository._write = explode
    try:
        with pytest.raises(RuntimeError), session_factory.begin() as session:
            sync_known_persons(session, records, replace=True)
    finally:
        repository._write = original

    assert _names(session_factory) == before


def test_the_operator_own_tables_are_never_replaced() -> None:
    """The sources and the officials are the operator's own tables. A row there is a
    decision, and no export from Airtable revokes it."""
    assert "sources" not in ShareTableClient.REPLACES_WHOLE
    assert "officials" not in ShareTableClient.REPLACES_WHOLE
    assert ShareTableClient.REPLACES_WHOLE == {"known_persons", "articles"}


def test_a_body_shorter_than_the_server_promised_is_not_a_list() -> None:
    from airtable.replace import IncompleteDownload

    with pytest.raises(IncompleteDownload, match="не полностью"):
        download_looks_complete(b"x" * 10, 100)


def test_a_body_of_the_promised_length_is_a_list() -> None:
    download_looks_complete(b"x" * 100, 100)
    download_looks_complete(b"x" * 100, None)


def test_a_collapse_against_the_last_known_size_is_refused() -> None:
    safe, reason = removal_is_safe(100, 10_000)

    assert not safe
    assert "похоже на оборванную выгрузку" in reason


def test_a_shrinking_that_stays_plausible_is_allowed() -> None:
    """A real list can be cut hard: people are removed in batches after an amnesty."""
    safe, reason = removal_is_safe(5_000, 10_000)

    assert safe and reason == ""


def test_a_tiny_export_is_refused_however_it_compares() -> None:
    safe, reason = removal_is_safe(3, 2)

    assert not safe
    assert str(MINIMUM_ROWS_TO_TRUST) in reason


def test_a_list_that_has_never_been_filled_may_be_any_size() -> None:
    """A guard against deletion must not stand in the way of a first write.

    With nothing in the table there is nothing to lose, so a list of a dozen people is
    simply a list of a dozen people. The floor bites only when there is a list to protect.
    """
    safe, reason = removal_is_safe(2, 0)

    assert safe and reason == ""


def test_articles_are_replaced_the_same_way(session_factory: sessionmaker[Session]) -> None:
    from airtable.articles import sync_articles

    with session_factory.begin() as session:
        sync_articles(
            session,
            [AirtableRecord(f"recA{n}", {"Полная статья": f"{n} УК РФ"}) for n in range(200)],
            replace=True,
        )
    with session_factory() as session:
        assert len(list(session.scalars(select(CriminalArticleRecord)))) == 200

    with session_factory.begin() as session:
        result = sync_articles(
            session,
            [AirtableRecord(f"recA{n}", {"Полная статья": f"{n} УК РФ"}) for n in range(120)],
            replace=True,
        )

    assert result.removed == 80
    with session_factory() as session:
        assert len(list(session.scalars(select(CriminalArticleRecord)))) == 120


def test_the_sync_creates_no_rosfinmonitoring_snapshot_at_all(
    session_factory: sessionmaker[Session],
) -> None:
    """The separation used to be enforced by filtering: a snapshot of an operator's own
    list was created and then kept out of every query that asked «who is absent from the
    published перечень».

    Now there is nothing to filter, because the sync creates no snapshot at all. The
    Airtable list of people we have on file says nothing about the published list, and a
    person on it is not a probable match — he is simply a person we already knew.
    """
    from support.airtable_fakes import FakeAirtable

    from airtable.service import AirtableSyncService, SyncSource
    from db.orm_models import RosfinmonitoringSnapshotRecord

    fake = FakeAirtable({"Known": [AirtableRecord("recK1", {"full_name": "Иван Иванов"})]})
    source = SyncSource(
        mode="api",
        client=fake,
        tables={
            "sources": "Sources",
            "known_persons": "Known",
            "officials": "Excluded",
            "articles": "Articles",
        },
    )

    AirtableSyncService(session_factory, source).sync()

    with session_factory() as session:
        assert list(session.scalars(select(RosfinmonitoringSnapshotRecord))) == []
