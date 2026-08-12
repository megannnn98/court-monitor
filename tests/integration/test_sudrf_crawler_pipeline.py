"""Integration test: full crawler → ingest → parse → extract pipeline."""

from __future__ import annotations

from pathlib import Path

from court_monitor.config.loader import SourceConfig, load_monitoring
from court_monitor.domain.models import SourceBackend, SourceType
from court_monitor.services import process_source
from court_monitor.storage import repository as repo

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "sudrf-live" / "2zovs" / "press"


def _make_config() -> SourceConfig:
    return SourceConfig(
        name="2zovs-test",
        type=SourceType.sudrf,
        backend=SourceBackend.fixture,
        base_url="https://2zovs.msk.sudrf.ru",
        fixture_path=str(FIXTURE_DIR),
        court_name="2-й Западный окружной военный суд",
        press_module="press_dep",
    )


def test_crawler_pipeline_end_to_end(db_session) -> None:
    """Test full flow: crawler → ingest → parse → extract."""
    config = _make_config()
    monitoring = load_monitoring()

    # First run: should ingest all fixtures
    stats = process_source(db_session, config, monitoring, parse_immediately=True)

    assert stats.fetched >= 4  # At least 4 release files
    assert stats.new_documents >= 4
    assert stats.parsed >= 1  # At least one should be relevant and parsed

    # Check that documents were created with proper metadata
    docs = repo.list_documents(db_session, limit=100)
    assert len(docs) >= 4

    # Find the Razlugo document (did=234)
    razlugo_doc = None
    for doc in docs:
        if doc.external_id == "234":
            razlugo_doc = doc
            break

    assert razlugo_doc is not None
    assert razlugo_doc.title is not None
    assert "Брянск" in razlugo_doc.title or "финансирование" in razlugo_doc.title
    assert razlugo_doc.published_at is not None
    assert razlugo_doc.published_at.year == 2026
    assert razlugo_doc.published_at.month == 4
    assert razlugo_doc.published_at.day == 2

    # Check that facts were extracted
    assert len(razlugo_doc.facts) > 0

    # Should have extracted name, article, date
    fact_fields = {f.field for f in razlugo_doc.facts}
    assert "full_name_original" in fact_fields or "title" in fact_fields


def test_crawler_incremental_fetch(db_session) -> None:
    """Test that incremental fetch skips already-known documents."""
    config = _make_config()
    monitoring = load_monitoring()

    # First run: ingest all
    stats1 = process_source(db_session, config, monitoring, parse_immediately=True)
    assert stats1.new_documents >= 4

    # Second run: should skip all (incremental)
    # In fixture mode, crawler skips known IDs, so fetched=0
    stats2 = process_source(
        db_session, config, monitoring, parse_immediately=True, full_rescan=False
    )
    assert stats2.fetched == 0  # All skipped in fixture mode
    assert stats2.new_documents == 0
    assert stats2.duplicates == 0  # Not even fetched

    # Third run with full_rescan: should re-fetch all
    stats3 = process_source(
        db_session, config, monitoring, parse_immediately=True, full_rescan=True
    )
    assert stats3.fetched >= 4
    assert stats3.duplicates >= 4  # Same content, so still duplicates


def test_list_known_external_ids(db_session) -> None:
    """Test repository function for listing known external_ids."""
    config = _make_config()
    monitoring = load_monitoring()

    # Initially no known IDs
    known_ids = repo.list_known_external_ids(db_session, source_id=config.name)
    assert len(known_ids) == 0

    # Ingest documents
    process_source(db_session, config, monitoring, parse_immediately=True)

    # Now should have known IDs
    known_ids = repo.list_known_external_ids(db_session, source_id=config.name)
    assert len(known_ids) >= 4
    assert "234" in known_ids
    assert "235" in known_ids
