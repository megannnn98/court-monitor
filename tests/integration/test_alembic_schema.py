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

        # Check that all expected tables exist
        tables = inspector.get_table_names()
        expected_tables = {
            "source_documents",
            "extracted_facts",
            "person_records",
            "person_match_candidates",
            "review_items",
            "audit_log",
            "jobs",
            "cases",
            "person_cases",
            "court_events",
        }
        for table in expected_tables:
            assert table in tables, f"Table {table} not created by alembic"

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


def test_alembic_downgrade_upgrade_cycle():
    """Verify that downgrade + upgrade cycle works without errors."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        db_url = f"sqlite:///{db_path}"

        # Upgrade to head
        _run_alembic_upgrade(db_url)

        # Downgrade to previous
        _run_alembic_downgrade(db_url, "0013")

        # Upgrade back to head
        _run_alembic_upgrade(db_url)

        # Verify tables exist
        engine = create_engine(db_url)
        inspector = inspect(engine)
        tables = inspector.get_table_names()
        assert "cases" in tables
        assert "person_cases" in tables
        assert "court_events" in tables
