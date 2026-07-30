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

import court_monitor.cli.app as cli_module
from court_monitor.cli.app import (
    _accumulate_fetch_stats,
    _doctor_check_alembic,
    _doctor_check_tables,
    _fetch_source_legacy,
    _render_stats_line,
    _render_totals_line,
    _stats_color,
)
from court_monitor.config.registry import SourceRegistryEntry
from court_monitor.services import SourceStats
from court_monitor.storage import repository as repo
from court_monitor.storage.orm import Base


def test_accumulate_fetch_stats_sums_fields():
    totals = {"fetched": 0, "new": 0, "dup": 0, "parsed": 0, "irr": 0, "fail": 0, "blocked": 0}
    stats_a = SourceStats(
        fetched=5, new_documents=3, duplicates=2, parsed=2, irrelevant=1, failed=0, blocked=0
    )
    stats_b = SourceStats(
        fetched=4, new_documents=1, duplicates=3, parsed=0, irrelevant=0, failed=1, blocked=1
    )

    _accumulate_fetch_stats(totals, stats_a)
    _accumulate_fetch_stats(totals, stats_b)

    assert totals == {
        "fetched": 9,
        "new": 4,
        "dup": 5,
        "parsed": 2,
        "irr": 1,
        "fail": 1,
        "blocked": 1,
    }


def test_accumulate_fetch_stats_does_not_touch_other_keys():
    totals = {"fetched": 0, "new": 0, "dup": 0, "parsed": 0, "irr": 0, "fail": 0, "blocked": 0}
    stats = SourceStats()  # all zero
    _accumulate_fetch_stats(totals, stats)
    assert all(v == 0 for v in totals.values())


def test_stats_color_clean_is_green():
    assert _stats_color(SourceStats(fetched=2, parsed=2)) == typer.colors.GREEN


def test_stats_color_failed_is_yellow():
    assert _stats_color(SourceStats(failed=1)) == typer.colors.YELLOW


def test_stats_color_blocked_is_yellow():
    assert _stats_color(SourceStats(blocked=1)) == typer.colors.YELLOW


def test_stats_color_skipped_is_yellow_even_if_clean():
    assert _stats_color(SourceStats(), skipped=True) == typer.colors.YELLOW


def test_render_stats_line_includes_all_fields():
    stats = SourceStats(
        fetched=2, new_documents=1, duplicates=1, parsed=1, irrelevant=0, failed=0, blocked=0
    )
    line = _render_stats_line(stats)
    assert line == ("fetched=2 new=1 duplicates=1 parsed=1 irrelevant=0 failed=0 blocked=0")


def test_render_totals_line_includes_all_fields():
    totals = {"fetched": 9, "new": 4, "dup": 5, "parsed": 2, "irr": 1, "fail": 1, "blocked": 1}
    line = _render_totals_line(totals)
    assert line == ("fetched=9 new=4 duplicates=5 parsed=2 irrelevant=1 failed=1 blocked=1")


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

    monkeypatch.setattr(cli_module, "make_engine", _BrokenEngine)
    problems: list[str] = []

    _doctor_check_tables(problems)

    assert any("table inspection failed" in p for p in problems)


def test_doctor_check_alembic_reports_errors(monkeypatch):
    def _raise_status(_url):
        raise RuntimeError("migration boom")

    monkeypatch.setattr(cli_module, "revision_status", _raise_status)
    problems: list[str] = []

    _doctor_check_alembic(problems)

    assert any("revision check failed" in p for p in problems)


def test_doctor_check_alembic_reports_a_database_behind_head(monkeypatch):
    """Drift raises nothing — the old tables are all still there — so it has
    to be detected by comparing revisions, not by catching an exception."""
    monkeypatch.setattr(cli_module, "revision_status", lambda _url: ("0007_audit_log", "0008_rfm"))
    problems: list[str] = []

    _doctor_check_alembic(problems)

    assert any("behind migrations" in p for p in problems)


def test_doctor_check_alembic_passes_when_at_head(monkeypatch):
    monkeypatch.setattr(cli_module, "revision_status", lambda _url: ("0008_rfm", "0008_rfm"))
    problems: list[str] = []

    _doctor_check_alembic(problems)

    assert problems == []


def test_require_current_schema_aborts_when_behind(monkeypatch):
    """Must stop before any work: run-all otherwise fetches every source over
    the network and only then dies inside generate_matches."""
    monkeypatch.setattr(cli_module, "revision_status", lambda _url: ("0007_audit_log", "0008_rfm"))

    with pytest.raises(typer.Exit) as exc:
        cli_module._require_current_schema()
    assert exc.value.exit_code == 1


def test_require_current_schema_passes_at_head(monkeypatch):
    monkeypatch.setattr(cli_module, "revision_status", lambda _url: ("0008_rfm", "0008_rfm"))
    cli_module._require_current_schema()  # must not raise


def test_require_current_schema_does_not_block_on_check_failure(monkeypatch):
    """A broken revision lookup must not stop a run that would have worked."""

    def _boom(_url):
        raise RuntimeError("no alembic table")

    monkeypatch.setattr(cli_module, "revision_status", _boom)
    cli_module._require_current_schema()  # must not raise


def test_docker_build_filter_includes_all_dockerfile_inputs():
    workflow = yaml.safe_load(Path(".github/workflows/ci.yml").read_text(encoding="utf-8"))
    filters = workflow["jobs"]["changes"]["steps"][1]["with"]["filters"]

    assert "alembic.ini" in filters
