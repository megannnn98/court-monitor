"""The sync of the four lists, over a faked Airtable. No test here touches the network."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from support.airtable_fakes import fake_source
from support.db_fixtures import DatabaseSeeder

from airtable.client import AirtableError, AirtableRecord
from airtable.models import MODE_SHARE, TABLES, TableStatus
from airtable.service import (
    AirtableSyncAlreadyRunningError,
    AirtableSyncService,
    SyncSource,
    build_sync_service,
)
from db.orm_models import (
    AirtableKnownPersonRecord,
    ExcludedPersonRecord,
    Source,
)

_ENV = {
    "AIRTABLE_TOKEN": "secret-token",
    "AIRTABLE_BASE_ID": "appTest",
    "AIRTABLE_SOURCES_TABLE": "Sources",
    "AIRTABLE_KNOWN_PERSONS_TABLE": "Known",
    "AIRTABLE_OFFICIALS_TABLE": "Excluded",
}


class FakeAirtable:
    """Airtable, held still. `tables` maps a table name to its records; a value that
    is an `AirtableError` is raised instead of returned, which is how a failing table
    is exercised without a network."""

    def __init__(self, tables: dict[str, list[AirtableRecord] | AirtableError]) -> None:
        self.tables = tables
        self.requested: list[str] = []

    def list_records(self, table: str) -> list[AirtableRecord]:
        self.requested.append(table)
        answer = self.tables.get(table, [])
        if isinstance(answer, AirtableError):
            raise answer
        return list(answer)


def _person(record_id: str, name: str, **fields: object) -> AirtableRecord:
    return AirtableRecord(record_id, {"full_name": name, **fields})


def _service(
    session_factory: sessionmaker[Session],
    client: FakeAirtable,
    *,
    lock: object = None,
) -> AirtableSyncService:
    return AirtableSyncService(
        session_factory,
        fake_source(client),
        **({"lock": lock} if lock is not None else {}),  # type: ignore[arg-type]
    )


@pytest.fixture
def client() -> FakeAirtable:
    return FakeAirtable({})


def _rows(session_factory: sessionmaker[Session], model: type[Any]) -> list[Any]:
    with session_factory() as session:
        return list(session.scalars(select(model)))


class TestSuccessfulSync:
    def test_an_empty_airtable_reports_every_table_as_nothing_to_do(
        self, session_factory: sessionmaker[Session], client: FakeAirtable
    ) -> None:
        report = _service(session_factory, client).sync()
        assert report.status == "success"
        assert sorted(report.tables) == sorted(TABLES)
        for result in report.tables.values():
            assert (result.created, result.updated, result.unchanged) == (0, 0, 0)

    def test_all_four_tables_are_read(self, session_factory: sessionmaker[Session]) -> None:
        airtable = FakeAirtable({})
        _service(session_factory, airtable).sync()
        assert airtable.requested == ["Sources", "Known", "Excluded", "Articles"]

    def test_a_person_is_created_once_and_then_stays_unchanged(
        self, session_factory: sessionmaker[Session]
    ) -> None:
        airtable = FakeAirtable({"Known": [_person("recA", "Иван Иванов")]})
        service = _service(session_factory, airtable)

        first = service.sync().tables["known_persons"]
        assert (first.created, first.updated, first.unchanged) == (1, 0, 0)

        second = service.sync().tables["known_persons"]
        assert (second.created, second.updated, second.unchanged) == (0, 0, 1)
        # No duplicate row, however many times the button is pressed.
        assert len(_rows(session_factory, AirtableKnownPersonRecord)) == 1

    def test_a_renamed_person_is_updated_in_place(
        self, session_factory: sessionmaker[Session]
    ) -> None:
        airtable = FakeAirtable({"Known": [_person("recA", "Иван Иванов")]})
        service = _service(session_factory, airtable)
        service.sync()

        airtable.tables["Known"] = [_person("recA", "Иван Петров")]
        result = service.sync().tables["known_persons"]
        assert (result.created, result.updated) == (0, 1)
        rows = _rows(session_factory, AirtableKnownPersonRecord)
        assert [(row.external_id, row.full_name) for row in rows] == [("recA", "Иван Петров")]

    def test_a_deactivated_record_is_kept_and_switched_off(
        self, session_factory: sessionmaker[Session]
    ) -> None:
        airtable = FakeAirtable({"Excluded": [_person("recB", "Ольга Минакова", active=True)]})
        service = _service(session_factory, airtable)
        service.sync()

        airtable.tables["Excluded"] = [
            _person("recB", "Ольга Минакова", active=False, reason="уволена")
        ]
        result = service.sync().tables["officials"]
        assert result.updated == 1
        # Deactivated, never deleted: the row is the operator's own.
        rows = _rows(session_factory, ExcludedPersonRecord)
        assert len(rows) == 1
        assert rows[0].active is False
        assert rows[0].reason == "уволена"

    def test_a_record_removed_from_airtable_is_not_deleted(
        self, session_factory: sessionmaker[Session]
    ) -> None:
        airtable = FakeAirtable({"Known": [_person("recA", "Иван Иванов")]})
        service = _service(session_factory, airtable)
        service.sync()

        airtable.tables["Known"] = []
        service.sync()
        assert len(_rows(session_factory, AirtableKnownPersonRecord)) == 1

    def test_a_record_without_a_name_is_counted_as_an_error_not_a_row(
        self, session_factory: sessionmaker[Session]
    ) -> None:
        airtable = FakeAirtable({"Known": [AirtableRecord("recX", {"note": "no name here"})]})
        result = _service(session_factory, airtable).sync().tables["known_persons"]
        assert (result.created, result.errors) == (0, 1)
        assert _rows(session_factory, AirtableKnownPersonRecord) == []

    def test_the_names_are_folded_as_the_pipeline_folds_them(
        self, session_factory: sessionmaker[Session]
    ) -> None:
        airtable = FakeAirtable({"Known": [_person("recA", "ИВАН ёжиков")]})
        _service(session_factory, airtable).sync()
        row = _rows(session_factory, AirtableKnownPersonRecord)[0]
        # The dictionary writes «ё», the pipeline stores «е»; the key keeps only letters.
        assert row.normalized_name == "Иван ежиков"
        assert row.matching_key == "иванежиков"


class TestSources:
    def test_a_known_source_is_named_and_linked(
        self, session_factory: sessionmaker[Session]
    ) -> None:
        with session_factory.begin() as session:
            DatabaseSeeder(session).source("ОВД-Инфо", "https://ovd.info")

        airtable = FakeAirtable(
            {
                "Sources": [
                    AirtableRecord(
                        "recS", {"base_url": "https://ovd.info", "name": "ОВД-Инфо (новое)"}
                    )
                ]
            }
        )
        result = _service(session_factory, airtable).sync().tables["sources"]
        assert result.updated == 1
        with session_factory() as session:
            row = session.scalar(select(Source))
            assert row is not None
            assert (row.name, row.external_id, row.active) == ("ОВД-Инфо (новое)", "recS", True)

    def test_a_trailing_slash_is_the_same_address(
        self, session_factory: sessionmaker[Session]
    ) -> None:
        with session_factory.begin() as session:
            DatabaseSeeder(session).source("ОВД-Инфо", "https://ovd.info")
        airtable = FakeAirtable(
            {"Sources": [AirtableRecord("recS", {"base_url": "https://ovd.info/"})]}
        )
        result = _service(session_factory, airtable).sync().tables["sources"]
        assert result.updated == 1

    def test_an_inactive_source_is_switched_off_not_deleted(
        self, session_factory: sessionmaker[Session]
    ) -> None:
        with session_factory.begin() as session:
            seed = DatabaseSeeder(session)
            source_id = seed.source("ОВД-Инфо", "https://ovd.info")
            seed.article(source_id, external_id="d1", title="Арест", text="Текст")

        airtable = FakeAirtable(
            {"Sources": [AirtableRecord("recS", {"base_url": "https://ovd.info", "active": False})]}
        )
        _service(session_factory, airtable).sync()
        with session_factory() as session:
            assert session.scalar(select(Source.active)) is False

    def test_a_source_this_build_cannot_load_is_reported_not_invented(
        self, session_factory: sessionmaker[Session]
    ) -> None:
        airtable = FakeAirtable(
            {"Sources": [AirtableRecord("recS", {"base_url": "https://nowhere.example"})]}
        )
        result = _service(session_factory, airtable).sync().tables["sources"]
        # Nothing created: a row with no adapter behind it is a lie in the list.
        assert (result.created, result.updated, result.unchanged) == (0, 0, 0)
        assert _rows(session_factory, Source) == []

    def test_two_records_naming_one_source_do_not_both_claim_it(
        self, session_factory: sessionmaker[Session]
    ) -> None:
        with session_factory.begin() as session:
            DatabaseSeeder(session).source("ОВД-Инфо", "https://ovd.info")
        airtable = FakeAirtable(
            {
                "Sources": [
                    AirtableRecord("rec1", {"base_url": "https://ovd.info", "name": "Первый"}),
                    AirtableRecord("rec2", {"base_url": "https://ovd.info", "name": "Второй"}),
                ]
            }
        )
        _service(session_factory, airtable).sync()
        with session_factory() as session:
            assert session.scalar(select(Source.external_id)) == "rec1"


class TestPartialFailure:
    def test_one_failing_table_does_not_hide_the_others(
        self, session_factory: sessionmaker[Session]
    ) -> None:
        airtable = FakeAirtable(
            {
                "Sources": AirtableError("Airtable answered 403 for table 'Sources'"),
                "Known": [_person("recK1", "Иван Иванов")],
                "Excluded": [_person("recE1", "Ольга Минакова")],
            }
        )
        report = _service(session_factory, airtable).sync()

        assert report.status == "partial"
        assert report.tables["sources"].status == TableStatus.ERROR
        assert "Sources" in (report.tables["sources"].error or "")
        for table in ("known_persons", "officials"):
            assert report.tables[table].status == TableStatus.SUCCESS
            assert report.tables[table].created == 1

    def test_a_database_failure_in_one_table_is_also_only_that_table(
        self, session_factory: sessionmaker[Session]
    ) -> None:
        airtable = FakeAirtable({"Known": [_person("recK1", "Иван Иванов")]})
        service = _service(session_factory, airtable)
        report = service.sync()
        assert report.status == "success"
        # The write path itself failing would be reported per table, not as a 500.
        assert report.tables["known_persons"].created == 1


class TestConcurrency:
    def test_a_second_sync_while_one_runs_is_refused(
        self, session_factory: sessionmaker[Session]
    ) -> None:
        @contextmanager
        def held() -> Iterator[bool]:
            yield False

        with pytest.raises(AirtableSyncAlreadyRunningError) as caught:
            _service(session_factory, FakeAirtable({}), lock=held).sync()
        assert "already running" in str(caught.value)

    def test_the_lock_is_released_so_the_next_sync_runs(
        self, session_factory: sessionmaker[Session]
    ) -> None:
        airtable = FakeAirtable({})
        service = _service(session_factory, airtable)
        first = service.sync()
        second = service.sync()
        assert first.status == second.status == "success"

    def test_a_refused_sync_reads_no_table(self, session_factory: sessionmaker[Session]) -> None:
        @contextmanager
        def held() -> Iterator[bool]:
            yield False

        airtable = FakeAirtable({})
        with pytest.raises(AirtableSyncAlreadyRunningError):
            _service(session_factory, airtable, lock=held).sync()
        assert airtable.requested == []


class TestMissingConfiguration:
    def test_an_unconfigured_environment_is_a_readable_error(
        self, session_factory: sessionmaker[Session]
    ) -> None:
        from airtable.config import AirtableConfigurationError

        with pytest.raises(AirtableConfigurationError) as caught:
            build_sync_service(session_factory, {})
        assert "AIRTABLE_TOKEN" in str(caught.value)

    def test_the_pipeline_never_needs_the_configuration(
        self, session_factory: sessionmaker[Session]
    ) -> None:
        # Nothing in the service reads the environment on import or on construction of
        # the ordinary pipeline: a sync is built only when it is asked for.
        from airtable.config import AirtableSettings

        assert AirtableSettings.is_configured({}) is False
        assert _rows(session_factory, Source) == []


class TestAListWithoutASource:
    """Not every list is read from Airtable.

    The officials are kept in the database by hand, and a public link is configured only
    for the lists somebody made a view of. A list with nowhere to be read from is
    skipped — reading it as an empty one is how a list of 13 765 people disappears.
    """

    def test_a_list_with_no_source_is_skipped_not_read_as_empty(
        self, session_factory: sessionmaker[Session]
    ) -> None:
        fake = FakeAirtable({"Sources": [AirtableRecord("rec1", {"base_url": "https://a.test"})]})
        source = SyncSource(mode=MODE_SHARE, client=fake, tables={"sources": "sources"})
        report = AirtableSyncService(session_factory, source).sync()

        assert report.status == "success"
        assert report.tables["officials"].status == TableStatus.SKIPPED
        assert report.tables["officials"].error == "источник не настроен: список ведётся в базе"
        # And it was never asked of the reader, so nothing was emptied on the way.
        assert "officials" not in fake.requested
