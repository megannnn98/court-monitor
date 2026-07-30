"""Unit tests: small CLI helpers in cli/app.py.

The `run-all` command itself is thin orchestration over process_source /
process_registry_source / generate_matches (all tested elsewhere) plus
global config/engine lookups — same shape and same lack of direct CLI tests
as the pre-existing `fetch-all` command. Only the pure helper is unit-tested
here.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import typer
import yaml
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import court_monitor.cli._shared as shared
import court_monitor.cli.app as cli_module
import court_monitor.cli.commands.db as db_commands
from court_monitor.cli.app import _fetch_source_legacy, _stats_color
from court_monitor.cli.commands.db import _doctor_check_alembic, _doctor_check_tables
from court_monitor.config.registry import SourceRegistryEntry
from court_monitor.services import SourceStats
from court_monitor.storage import repository as repo
from court_monitor.storage.orm import Base


def test_stats_color_clean_is_green():
    assert _stats_color(SourceStats(fetched=2, parsed=2)) == typer.colors.GREEN


def test_stats_color_failed_is_yellow():
    assert _stats_color(SourceStats(failed=1)) == typer.colors.YELLOW


def test_stats_color_blocked_is_yellow():
    assert _stats_color(SourceStats(blocked=1)) == typer.colors.YELLOW


def test_stats_color_skipped_is_yellow_even_if_clean():
    assert _stats_color(SourceStats(), skipped=True) == typer.colors.YELLOW


def _make_engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(engine)
    return engine


def _registry_entry() -> SourceRegistryEntry:
    return SourceRegistryEntry(
        id="extremizmunet",
        name="Экстремизму - НЕТ!",
        url="https://t.me/extremizmunet",
        domain="t.me",
        source_type="telegram",
        username="extremizmunet",
    )


def test_fetch_source_legacy_registry_dry_run_rolls_back(monkeypatch):
    engine = _make_engine()
    monkeypatch.setattr(cli_module, "make_engine", lambda: engine)
    monkeypatch.setattr(cli_module, "_registry_entry_or_none", lambda _name: _registry_entry())

    _fetch_source_legacy("extremizmunet", dry_run=True, limit=1)

    with Session(engine) as session:
        assert repo.count_documents(session) == 0


def test_fetch_source_legacy_registry_limit_and_no_parse(monkeypatch):
    engine = _make_engine()
    monkeypatch.setattr(cli_module, "make_engine", lambda: engine)
    monkeypatch.setattr(cli_module, "_registry_entry_or_none", lambda _name: _registry_entry())

    _fetch_source_legacy("extremizmunet", limit=1, no_parse=True)

    with Session(engine) as session:
        docs = repo.list_documents(session)
        assert len(docs) == 1
        assert docs[0].parser_status == "pending"


def test_doctor_check_tables_reports_inspection_errors(monkeypatch):
    class _BrokenEngine:
        def connect(self):
            raise RuntimeError("boom")

    monkeypatch.setattr(db_commands, "make_engine", _BrokenEngine)

    problems = _doctor_check_tables()

    assert any("table inspection failed" in p for p in problems)


def test_doctor_check_alembic_reports_errors(monkeypatch):
    def _raise_status(_url):
        raise RuntimeError("migration boom")

    monkeypatch.setattr(db_commands, "revision_status", _raise_status)

    problems = _doctor_check_alembic()

    assert any("revision check failed" in p for p in problems)


def test_doctor_check_alembic_reports_a_database_behind_head(monkeypatch):
    """Drift raises nothing — the old tables are all still there — so it has
    to be detected by comparing revisions, not by catching an exception."""
    monkeypatch.setattr(db_commands, "revision_status", lambda _url: ("0007_audit_log", "0008_rfm"))

    problems = _doctor_check_alembic()

    assert any("behind migrations" in p for p in problems)


def test_doctor_check_alembic_passes_when_at_head(monkeypatch):
    monkeypatch.setattr(db_commands, "revision_status", lambda _url: ("0008_rfm", "0008_rfm"))

    assert _doctor_check_alembic() == []


def test_require_current_schema_aborts_when_behind(monkeypatch):
    """Must stop before any work: run-all otherwise fetches every source over
    the network and only then dies inside generate_matches."""
    monkeypatch.setattr(shared, "revision_status", lambda _url: ("0007_audit_log", "0008_rfm"))

    with pytest.raises(typer.Exit) as exc:
        shared.require_current_schema()
    assert exc.value.exit_code == 1


def test_require_current_schema_passes_at_head(monkeypatch):
    monkeypatch.setattr(shared, "revision_status", lambda _url: ("0008_rfm", "0008_rfm"))
    shared.require_current_schema()  # must not raise


def test_require_current_schema_does_not_block_on_check_failure(monkeypatch):
    """A broken revision lookup must not stop a run that would have worked."""

    def _boom(_url):
        raise RuntimeError("no alembic table")

    monkeypatch.setattr(shared, "revision_status", _boom)
    shared.require_current_schema()  # must not raise


def test_docker_build_filter_includes_all_dockerfile_inputs():
    workflow = yaml.safe_load(Path(".github/workflows/ci.yml").read_text(encoding="utf-8"))
    filters = workflow["jobs"]["changes"]["steps"][1]["with"]["filters"]

    assert "alembic.ini" in filters
