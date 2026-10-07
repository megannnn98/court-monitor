"""The file import mode: the same sync, with the records coming from CSVs.

A base that is only readable in a browser can still be exported by hand, and the export
dropped into `airtable-import/`. What matters here is not the parsing but the two rules
that keep a careless export harmless: a list with no file is skipped, and a file with no
rows is refused rather than read as an empty list.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from support.airtable_fakes import CONFIGURED_ENV, FakeAirtable, fake_source, file_source

from airtable.client import AirtableError
from airtable.config import AirtableConfigurationError
from airtable.files import FileTableClient, ImportSettings
from airtable.models import MODE_API, MODE_FILES, TABLES, TableStatus
from airtable.service import AirtableSyncService, build_sync_source
from db.orm_models import (
    AirtableKnownPersonRecord,
    ExcludedPersonRecord,
    Source,
)

NO_API = {name: "" for name in CONFIGURED_ENV}


def _write(directory: Path, name: str, body: str) -> Path:
    path = directory / f"{name}.csv"
    path.write_text(body, encoding="utf-8")
    return path


def _service(session_factory: sessionmaker[Session], directory: Path) -> AirtableSyncService:
    return AirtableSyncService(session_factory, file_source(directory))


class TestReading:
    def test_a_missing_file_is_skipped_not_an_error(self, tmp_path: Path) -> None:
        client = FileTableClient(ImportSettings(tmp_path))
        assert client.present() == {}
        with pytest.raises(FileNotFoundError):
            client.list_records("known_persons")

    def test_a_file_with_only_a_header_is_refused(self, tmp_path: Path) -> None:
        """The dangerous case: read as an empty list it would delete the snapshot."""
        _write(tmp_path, "known_persons", "ФИО,Дата рождения\n")
        with pytest.raises(AirtableError, match="пуст"):
            FileTableClient(ImportSettings(tmp_path)).list_records("known_persons")

    def test_rows_become_records(self, tmp_path: Path) -> None:
        _write(tmp_path, "known_persons", "ФИО,active\nИван Иванов,да\nОльга Минакова,false\n")
        records = FileTableClient(ImportSettings(tmp_path)).list_records("known_persons")
        assert [r.text("ФИО") for r in records] == ["Иван Иванов", "Ольга Минакова"]
        # The checkbox is read by name, in Russian as well as English.
        assert records[0].flag("active") is True
        assert records[1].flag("active") is False

    @pytest.mark.parametrize("value", ["true", "TRUE", "да", "1", "x", "вкл"])
    def test_truthy_checkbox_spellings(self, tmp_path: Path, value: str) -> None:
        _write(tmp_path, "known_persons", f"ФИО,Активен\nИван Иванов,{value}\n")
        record = FileTableClient(ImportSettings(tmp_path)).list_records("known_persons")[0]
        assert record.flag("Активен") is True

    @pytest.mark.parametrize("value", ["false", "нет", "0", "", "выкл"])
    def test_falsy_checkbox_spellings(self, tmp_path: Path, value: str) -> None:
        _write(tmp_path, "known_persons", f"ФИО,Активен\nИван Иванов,{value}\n")
        record = FileTableClient(ImportSettings(tmp_path)).list_records("known_persons")[0]
        assert record.flag("Активен") is False

    def test_a_bad_checkbox_is_inactive_not_active(self, tmp_path: Path) -> None:
        """Reading a misspelling as active would silently un-exclude a person."""
        _write(tmp_path, "officials", "ФИО,active\nИван Иванов,возможно\n")
        record = FileTableClient(ImportSettings(tmp_path)).list_records("officials")[0]
        assert record.flag("active") is False

    def test_a_bom_and_spaces_do_not_hide_a_column(self, tmp_path: Path) -> None:
        _write(tmp_path, "known_persons", "﻿ФИО ,  active \nИван Иванов,true\n")
        record = FileTableClient(ImportSettings(tmp_path)).list_records("known_persons")[0]
        assert record.text("ФИО") == "Иван Иванов"

    # Airtable writes dates in the locale of the view. Left unread, every birth date
    # arrives as NULL and namesakes become indistinguishable — silently.
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("July 12, 1980", "1980-07-12"),
            ("December 1, 1975", "1975-12-01"),
            ("1 July 1980", "1980-07-01"),
            ("12 июля 1980", "1980-07-12"),
            ("12.07.1980", "12.07.1980"),
            ("1980-07-12", "1980-07-12"),
            ("12/07/1980", "12/07/1980"),  # the pipeline reads these itself
        ],
    )
    def test_airtable_date_formats_become_iso(
        self, tmp_path: Path, value: str, expected: str
    ) -> None:
        _write(tmp_path, "known_persons", f'ФИО,Дата рождения\nИван Иванов,"{value}"\n')
        record = FileTableClient(ImportSettings(tmp_path)).list_records("known_persons")[0]
        assert record.text("Дата рождения") == expected

    @pytest.mark.parametrize("value", ["июнь 1980", "unknown", "12.1980", "????"])
    def test_a_date_the_reader_cannot_read_is_left_to_the_parser(
        self, tmp_path: Path, value: str
    ) -> None:
        """Not rewritten, not guessed: the pipeline's parser decides, and it refuses."""
        _write(tmp_path, "known_persons", f'ФИО,Дата рождения\nИван Иванов,"{value}"\n')
        record = FileTableClient(ImportSettings(tmp_path)).list_records("known_persons")[0]
        assert record.text("Дата рождения") == value

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("1 checked out of 1", True),
            ("0 checked out of 1", False),
            ("1 checked out of 2", True),
            ("0 checked out of 2", False),
        ],
    )
    def test_airtable_checkbox_spelling(self, tmp_path: Path, value: str, expected: bool) -> None:
        """How Airtable's export actually writes a checkbox."""
        _write(tmp_path, "known_persons", f"ФИО,Активен\nИван Иванов,{value}\n")
        record = FileTableClient(ImportSettings(tmp_path)).list_records("known_persons")[0]
        assert record.flag("Активен") is expected

    def test_airtable_own_membership_column_is_a_checkbox(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "known_persons",
            "Преследуемый,\u2726Росфинмониторинг\nИванов И. И.,1 checked out of 1\n",
        )
        record = FileTableClient(ImportSettings(tmp_path)).list_records("known_persons")[0]
        assert record.flag("\u2726Росфинмониторинг") is True

    def test_the_synthesized_id_is_stable_across_reads(self, tmp_path: Path) -> None:
        _write(tmp_path, "known_persons", "ФИО\nИван Иванов\n")
        client = FileTableClient(ImportSettings(tmp_path))
        assert (
            client.list_records("known_persons")[0].id == client.list_records("known_persons")[0].id
        )

    def test_namesakes_get_different_ids(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "known_persons",
            "ФИО,Дата рождения\nИванов Иван Иванович,01.01.1970\nИванов Иван Иванович,02.02.1980\n",
        )
        records = FileTableClient(ImportSettings(tmp_path)).list_records("known_persons")
        assert records[0].id != records[1].id

    def test_two_lists_never_share_an_id_for_one_name(self, tmp_path: Path) -> None:
        _write(tmp_path, "known_persons", "ФИО\nИван Иванов\n")
        _write(tmp_path, "officials", "ФИО\nИван Иванов\n")
        client = FileTableClient(ImportSettings(tmp_path))
        known = client.list_records("known_persons")[0]
        excluded = client.list_records("officials")[0]
        # Same person, two lists: the ids must not collide, or the two tables would
        # claim each other's rows.
        assert known.id != excluded.id


class TestModeSelection:
    def test_the_api_wins_when_it_is_configured(self, tmp_path: Path) -> None:
        _write(tmp_path, "known_persons", "ФИО\nИван Иванов\n")
        source = build_sync_source({**CONFIGURED_ENV, "AIRTABLE_IMPORT_DIR": str(tmp_path)})
        assert source.mode == MODE_API
        assert source.table_for("known_persons") == CONFIGURED_ENV["AIRTABLE_KNOWN_PERSONS_TABLE"]

    def test_files_are_used_when_the_api_is_not_set_up(self, tmp_path: Path) -> None:
        _write(tmp_path, "known_persons", "ФИО\nИван Иванов\n")
        source = build_sync_source({**NO_API, "AIRTABLE_IMPORT_DIR": str(tmp_path)})
        assert source.mode == MODE_FILES
        # Addressed by its own name, which is the file's stem.
        assert source.table_for("known_persons") == "known_persons"

    def test_neither_available_is_a_readable_error(self, tmp_path: Path) -> None:
        with pytest.raises(AirtableConfigurationError):
            build_sync_source({**NO_API, "AIRTABLE_IMPORT_DIR": str(tmp_path)})

    def test_an_empty_folder_is_not_enough_to_enable_a_sync(self, tmp_path: Path) -> None:
        with pytest.raises(AirtableConfigurationError):
            build_sync_source({**NO_API, "AIRTABLE_IMPORT_DIR": str(tmp_path)})


class TestSync:
    def test_only_the_exported_lists_are_touched(
        self, session_factory: sessionmaker[Session], tmp_path: Path
    ) -> None:
        _write(tmp_path, "known_persons", "ФИО\nИван Иванов\n")
        report = _service(session_factory, tmp_path).sync()

        assert report.mode == MODE_FILES
        assert report.tables["known_persons"].created == 1
        for name in ("sources", "officials", "articles"):
            assert report.tables[name].status == TableStatus.SKIPPED
        assert report.status == "success"
        with session_factory() as session:
            assert [
                row.full_name for row in session.scalars(select(AirtableKnownPersonRecord))
            ] == ["Иван Иванов"]

    def test_a_second_import_of_the_same_file_changes_nothing(
        self, session_factory: sessionmaker[Session], tmp_path: Path
    ) -> None:
        _write(tmp_path, "officials", "ФИО,Категория\nОльга Минакова,judge\n")
        service = _service(session_factory, tmp_path)
        assert service.sync().tables["officials"].created == 1
        again = service.sync().tables["officials"]
        assert (again.created, again.updated, again.unchanged) == (0, 0, 1)
        with session_factory() as session:
            assert len(list(session.scalars(select(ExcludedPersonRecord)))) == 1

    def test_an_empty_file_cannot_empty_the_list(
        self, session_factory: sessionmaker[Session], tmp_path: Path
    ) -> None:
        """The one accident that would cost the most: an export that came out empty."""
        _write(tmp_path, "known_persons", "ФИО\nПетров Петр Петрович\n")
        _service(session_factory, tmp_path).sync()
        with session_factory() as session:
            before = list(session.scalars(select(AirtableKnownPersonRecord)))

        _write(tmp_path, "known_persons", "ФИО\n")
        result = _service(session_factory, tmp_path).sync().tables["known_persons"]

        assert result.status == TableStatus.ERROR
        assert "пуст" in (result.error or "")
        with session_factory() as session:
            after = list(session.scalars(select(AirtableKnownPersonRecord)))
        assert [row.id for row in after] == [row.id for row in before]

    def test_a_list_the_operator_emptied_really_is_emptied(
        self, session_factory: sessionmaker[Session], tmp_path: Path
    ) -> None:
        """Not the same as an empty file: one row is removed and the file still has rows."""
        _write(tmp_path, "known_persons", "ФИО\nИван Иванов\nОльга Минакова\n")
        service = _service(session_factory, tmp_path)
        service.sync()
        _write(tmp_path, "known_persons", "ФИО\nИван Иванов\n")
        result = service.sync().tables["known_persons"]
        with session_factory() as session:
            names = {row.full_name for row in session.scalars(select(AirtableKnownPersonRecord))}
        assert "Ольга Минакова" in names  # a curated list is never emptied by a sync
        assert result.unchanged == 1

    def test_one_bad_file_does_not_hide_the_others(
        self, session_factory: sessionmaker[Session], tmp_path: Path
    ) -> None:
        _write(tmp_path, "known_persons", "ФИО\nИван Иванов\n")
        _write(tmp_path, "officials", "ФИО\n")
        report = _service(session_factory, tmp_path).sync()
        assert report.tables["known_persons"].created == 1
        assert report.tables["officials"].status == TableStatus.ERROR
        # One list in, one list refused: the good one is still reported.
        assert report.status == "partial"

    def test_the_report_names_the_mode(self, session_factory: sessionmaker[Session]) -> None:
        service = AirtableSyncService(
            session_factory, fake_source(FakeAirtable({}), mode=MODE_FILES)
        )
        assert service.sync().mode == MODE_FILES

    def test_every_list_has_a_file_name(self) -> None:
        for name in TABLES:
            assert FileTableClient(ImportSettings(Path("."))).path_for(name).name == (f"{name}.csv")


class TestSourcesFromFiles:
    def test_a_known_source_is_matched_by_its_address(
        self, session_factory: sessionmaker[Session], tmp_path: Path
    ) -> None:
        from support.db_fixtures import DatabaseSeeder

        with session_factory.begin() as session:
            DatabaseSeeder(session).source("ОВД-Инфо", "https://ovd.info")
        _write(
            tmp_path, "sources", "Ссылка,Название,active\nhttps://ovd.info,ОВД-Инфо (новое),да\n"
        )
        result = _service(session_factory, tmp_path).sync().tables["sources"]
        assert result.updated == 1
        with session_factory() as session:
            assert session.scalar(select(Source.name)) == "ОВД-Инфо (новое)"

    def test_an_empty_file_cannot_switch_every_source_off(
        self, session_factory: sessionmaker[Session], tmp_path: Path
    ) -> None:
        from support.db_fixtures import DatabaseSeeder

        with session_factory.begin() as session:
            DatabaseSeeder(session).source("ОВД-Инфо", "https://ovd.info")
        _write(tmp_path, "sources", "Ссылка,Название,active\n")
        result = _service(session_factory, tmp_path).sync().tables["sources"]
        assert result.status == TableStatus.ERROR
        with session_factory() as session:
            assert session.scalar(select(Source.active)) is True
