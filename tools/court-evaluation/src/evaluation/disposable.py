"""The tables a disposable test or evaluation database is wiped of: each owner's, as its
testing API names them (ADR 0031)."""

from __future__ import annotations

from sqlalchemy import Engine

from analysis import testing as analysis_testing
from cases import testing as cases_testing
from db.maintenance import PLATFORM_TABLES, truncate_disposable_tables
from ingestion import testing as ingestion_testing
from investigation import testing as investigation_testing
from orchestrator import testing as orchestrator_testing
from persons import testing as persons_testing
from processing import testing as processing_testing
from results import testing as results_testing

DISPOSABLE_TABLES = (
    *PLATFORM_TABLES,
    *investigation_testing.DISPOSABLE_TABLES,
    *cases_testing.DISPOSABLE_TABLES,
    *results_testing.DISPOSABLE_TABLES,
    *orchestrator_testing.DISPOSABLE_TABLES,
    *analysis_testing.DISPOSABLE_TABLES,
    *persons_testing.DISPOSABLE_TABLES,
    *processing_testing.DISPOSABLE_TABLES,
    *ingestion_testing.DISPOSABLE_TABLES,
)


def wipe(engine: Engine) -> None:
    """Every disposable table of a disposable database, emptied."""
    truncate_disposable_tables(engine, DISPOSABLE_TABLES)
