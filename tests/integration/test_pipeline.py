"""Integration test: full vertical slice — fixture press-release → DB → extracted facts.

Covers spec §26 MVP points: ingest, dedup, relevance, extraction, DB write.
No network: everything runs on saved HTML fixtures.
"""

from __future__ import annotations

from court_monitor.config.loader import SourceConfig
from court_monitor.domain.models import ParserStatus, SourceBackend, SourceType
from court_monitor.services import ingest_fetch_result, process_source, reprocess
from court_monitor.sources.base import FetchResult
from court_monitor.sources.sudrf import SudrfAdapter
from court_monitor.storage import repository as repo


def _make_source(fixture_dir: str) -> SourceConfig:
    return SourceConfig(
        name="2zovs-test",
        type=SourceType.sudrf,
        backend=SourceBackend.fixture,
        base_url="https://2zovs.sudrf.ru",
        paths=("/modules.php?name=press",),
        fixture_path=fixture_dir,
        parser="sudrf_press",
        enabled=True,
    )


def test_pipeline_ingests_fixtures_and_extracts_facts(
    db_session, monitoring_cfg, fixtures_html_dir
):
    source = _make_source(str(fixtures_html_dir))
    stats = process_source(db_session, source, monitoring_cfg)

    # Two fixtures (relevant + irrelevant) are fetched and ingested.
    assert stats.fetched == 2
    assert stats.new_documents == 2
    assert stats.parsed == 1  # only the relevant one
    assert stats.irrelevant == 1
    assert stats.failed == 0

    docs = repo.list_documents(db_session)
    assert len(docs) == 2

    relevant = next(d for d in docs if d.parser_status == ParserStatus.parsed.value)
    irrelevant = next(d for d in docs if d.parser_status == ParserStatus.irrelevant.value)

    # The relevant document has the expected extracted facts.
    fields = {(f.field, str(f.value)) for f in relevant.facts}
    assert ("criminal_article", "205.1") in fields
    assert ("matched_keyword", "финансирование терроризма") in fields
    assert any(field == "title" for field, _ in fields)
    assert any(field == "published_at" for field, _ in fields)
    assert any(field == "full_name_original" for field, _ in fields)

    # published_at parsed as 2026-04-02.
    assert relevant.published_at is not None
    assert (relevant.published_at.year, relevant.published_at.month, relevant.published_at.day) == (
        2026,
        4,
        2,
    )

    # The irrelevant document has no matched article/keyword facts.
    assert all(f.field not in {"matched_article", "matched_keyword"} for f in irrelevant.facts)


def test_dedup_second_run_creates_nothing(db_session, monitoring_cfg, fixtures_html_dir):
    source = _make_source(str(fixtures_html_dir))
    first = process_source(db_session, source, monitoring_cfg)
    assert first.new_documents == 2

    second = process_source(db_session, source, monitoring_cfg)
    assert second.fetched == 2
    assert second.new_documents == 0
    assert second.duplicates == 2
    assert repo.count_documents(db_session) == 2


def test_ingest_fetch_result_dedup_directly(db_session):
    result = FetchResult.from_content(
        url="https://example/x",
        content="<html><body>ст. 205.1 УК РФ</body></html>",
        source_type=SourceType.sudrf,
        source_name="t",
    )
    doc1, created1 = ingest_fetch_result(db_session, result)
    doc2, created2 = ingest_fetch_result(db_session, result)
    db_session.flush()
    assert created1 is True
    assert created2 is False
    assert doc1.id == doc2.id


def test_reprocess_refreshes_facts(db_session, monitoring_cfg, fixtures_html_dir):
    source = _make_source(str(fixtures_html_dir))
    process_source(db_session, source, monitoring_cfg)
    relevant = next(
        d for d in repo.list_documents(db_session) if d.parser_status == ParserStatus.parsed.value
    )
    before = len(relevant.facts)

    new_id = reprocess(db_session, relevant.id)
    db_session.refresh(relevant)
    assert new_id == relevant.id
    after = len(relevant.facts)
    assert after >= before  # facts rebuilt; count should be stable for same content
    assert any(f.field == "criminal_article" for f in relevant.facts)


def test_sudrf_adapter_fixture_mode_yields_results(fixtures_html_dir):
    adapter = SudrfAdapter(_make_source(str(fixtures_html_dir)))
    results = list(adapter.fetch_new())
    assert len(results) == 2
    assert all(r.source_type == SourceType.sudrf for r in results)
    assert all(r.content_hash for r in results)


# ---------------------------------------------------------------------------
# Etap 2: fetch → pending → parse-pending → parsed
# ---------------------------------------------------------------------------


def test_fetch_no_parse_leaves_documents_pending(db_session, monitoring_cfg, fixtures_html_dir):
    """fetch-source --no-parse: documents saved with parser_status=pending."""
    from court_monitor.services import process_pending

    source = _make_source(str(fixtures_html_dir))
    stats = process_source(db_session, source, monitoring_cfg, parse_immediately=False)

    assert stats.fetched == 2
    assert stats.new_documents == 2
    assert stats.parsed == 0
    assert stats.irrelevant == 0

    docs = repo.list_documents(db_session)
    assert len(docs) == 2
    assert all(d.parser_status == ParserStatus.pending.value for d in docs)


def test_parse_pending_calls_sudrf_press(db_session, monitoring_cfg, fixtures_html_dir):
    """parse-pending processes pending documents through sudrf_press parser."""
    from court_monitor.services import process_pending

    source = _make_source(str(fixtures_html_dir))
    process_source(db_session, source, monitoring_cfg, parse_immediately=False)

    pending = repo.list_pending_documents(db_session)
    assert len(pending) == 2

    stats = process_pending(db_session, monitoring_cfg)
    assert stats.parsed == 1
    assert stats.irrelevant == 1
    assert stats.failed == 0

    assert repo.count_pending_documents(db_session) == 0


def test_parse_pending_saves_title_date_text(db_session, monitoring_cfg, fixtures_html_dir):
    """Parsed document has title, published_at, text fields populated."""
    from court_monitor.services import process_pending

    source = _make_source(str(fixtures_html_dir))
    process_source(db_session, source, monitoring_cfg, parse_immediately=False)
    process_pending(db_session, monitoring_cfg)

    docs = repo.list_documents(db_session)
    relevant = next(d for d in docs if d.parser_status == ParserStatus.parsed.value)

    assert relevant.title is not None
    assert "приговор" in relevant.title.lower() or "терроризм" in relevant.title.lower()
    assert relevant.published_at is not None
    assert relevant.published_at.year == 2026
    assert relevant.published_at.month == 4
    assert relevant.published_at.day == 2
    assert relevant.text is not None
    assert len(relevant.text) > 0


def test_parse_pending_status_transition(db_session, monitoring_cfg, fixtures_html_dir):
    """Document status transitions: pending → parsed (or irrelevant)."""
    from court_monitor.services import process_pending

    source = _make_source(str(fixtures_html_dir))
    process_source(db_session, source, monitoring_cfg, parse_immediately=False)

    docs_before = repo.list_documents(db_session)
    assert all(d.parser_status == ParserStatus.pending.value for d in docs_before)

    process_pending(db_session, monitoring_cfg)

    docs_after = repo.list_documents(db_session)
    statuses = {d.parser_status for d in docs_after}
    assert ParserStatus.pending.value not in statuses
    assert ParserStatus.parsed.value in statuses or ParserStatus.irrelevant.value in statuses


def test_parse_pending_skips_already_parsed(db_session, monitoring_cfg, fixtures_html_dir):
    """Second parse-pending run processes zero documents (idempotent)."""
    from court_monitor.services import process_pending

    source = _make_source(str(fixtures_html_dir))
    process_source(db_session, source, monitoring_cfg, parse_immediately=False)

    stats1 = process_pending(db_session, monitoring_cfg)
    assert stats1.parsed + stats1.irrelevant == 2

    stats2 = process_pending(db_session, monitoring_cfg)
    assert stats2.parsed == 0
    assert stats2.irrelevant == 0
    assert stats2.failed == 0
