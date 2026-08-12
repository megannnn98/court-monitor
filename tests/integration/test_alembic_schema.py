"""Test that Alembic migrations produce schema matching ORM metadata.

This test catches drift between migrations and ORM models — a common source
of subtle bugs where `Base.metadata.create_all()` works but `alembic upgrade head`
produces a different schema.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from court_monitor.storage.orm import Base


def _run_alembic_upgrade(db_url: str, revision: str = "head") -> None:
    """Run alembic upgrade programmatically."""
    alembic_cfg = Config()
    alembic_cfg.set_main_option("script_location", "migrations")
    alembic_cfg.set_main_option("sqlalchemy.url", db_url)
    command.upgrade(alembic_cfg, revision)


def _run_alembic_downgrade(db_url: str, revision: str) -> None:
    """Run alembic downgrade programmatically."""
    alembic_cfg = Config()
    alembic_cfg.set_main_option("script_location", "migrations")
    alembic_cfg.set_main_option("sqlalchemy.url", db_url)
    command.downgrade(alembic_cfg, revision)


def test_alembic_upgrade_head_creates_valid_schema():
    """Verify that alembic upgrade head creates all tables with correct FKs."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        db_url = f"sqlite:///{db_path}"

        # Run alembic upgrade head
        _run_alembic_upgrade(db_url)

        # Connect and verify schema
        engine = create_engine(db_url)
        inspector = inspect(engine)

        # The ORM ``Base.metadata.tables`` is the single source of truth for
        # application tables. Alembic must produce the same set (minus
        # ``alembic_version``, which is an alembic-internal table not owned by
        # the ORM).
        tables = set(inspector.get_table_names())
        expected_tables = set(Base.metadata.tables.keys())
        assert tables - {"alembic_version"} == expected_tables, (
            f"Tables drifted between ORM and alembic. "
            f"Missing in DB: {expected_tables - tables}; "
            f"Extra in DB: {tables - expected_tables - {'alembic_version'}}"
        )

        # Check person_cases has correct FK to person_records (not persons)
        fk_list = inspector.get_foreign_keys("person_cases")
        person_id_fk = next(
            (fk for fk in fk_list if "person_id" in fk["constrained_columns"]), None
        )
        assert person_id_fk is not None, "person_cases.person_id has no FK"
        assert person_id_fk["referred_table"] == "person_records", (
            f"person_cases.person_id FK points to {person_id_fk['referred_table']}, "
            "expected person_records"
        )

        # Verify FK enforcement works
        with Session(engine) as session:
            # Enable FK enforcement for SQLite
            session.execute(text("PRAGMA foreign_keys = ON"))

            # Try to insert person_cases with non-existent person_id
            # This should fail if FK is enforced
            session.execute(
                text("INSERT INTO cases (court, case_number) VALUES ('test', '1-1/2026')")
            )
            session.commit()

            with pytest.raises(IntegrityError):
                session.execute(
                    text("INSERT INTO person_cases (person_id, case_id) VALUES (999, 1)")
                )
                session.commit()


def test_alembic_court_processing_fk_chain():
    """Verify the new FK chain for court processing tables (task §24)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        db_url = f"sqlite:///{db_path}"
        _run_alembic_upgrade(db_url)

        engine = create_engine(db_url)
        inspector = inspect(engine)

        # CaseParticipant FKs
        cp_fks = {
            tuple(fk["constrained_columns"]): fk
            for fk in inspector.get_foreign_keys("case_participants")
        }
        assert ("case_id",) in cp_fks
        assert cp_fks[("case_id",)]["referred_table"] == "cases"
        assert ("source_document_id",) in cp_fks
        assert cp_fks[("source_document_id",)]["referred_table"] == "source_documents"

        # CaseMatchCandidate FKs
        cmc_fks = {
            tuple(fk["constrained_columns"]): fk
            for fk in inspector.get_foreign_keys("case_match_candidates")
        }
        assert ("case_id",) in cmc_fks
        assert cmc_fks[("case_id",)]["referred_table"] == "cases"
        assert ("source_document_id",) in cmc_fks
        assert cmc_fks[("source_document_id",)]["referred_table"] == "source_documents"

        # ReviewItem → CaseMatchCandidate (new FK, task §10)
        ri_fks = {
            tuple(fk["constrained_columns"]): fk
            for fk in inspector.get_foreign_keys("review_items")
        }
        assert ("case_match_candidate_id",) in ri_fks
        assert ri_fks[("case_match_candidate_id",)]["referred_table"] == "case_match_candidates"

        # CourtDocumentProcessing FK chain (task §14)
        cdp_fks = {
            tuple(fk["constrained_columns"]): fk
            for fk in inspector.get_foreign_keys("court_document_processing")
        }
        assert ("document_id",) in cdp_fks
        assert cdp_fks[("document_id",)]["referred_table"] == "source_documents"


def test_alembic_downgrade_upgrade_cycle():
    """Verify that downgrade + upgrade cycle works without errors."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        db_url = f"sqlite:///{db_path}"

        # Upgrade to head
        _run_alembic_upgrade(db_url)

        # Downgrade to the last pre-court-processing revision; exercising a
        # further downgrade (through 0014..0001) is covered by the dedicated
        # ``make migrate-reset`` workflow, not by this smoke test.
        _run_alembic_downgrade(db_url, "0016_add_case_participant")

        # Upgrade back to head
        _run_alembic_upgrade(db_url)

        # Full set of tables must reappear
        engine = create_engine(db_url)
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        expected_tables = set(Base.metadata.tables.keys())
        assert tables - {"alembic_version"} == expected_tables
