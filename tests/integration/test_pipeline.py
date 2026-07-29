"""Integration test: full vertical slice — fixture press-release → DB → extracted facts.

Covers spec §26 MVP points: ingest, dedup, relevance, extraction, DB write.
No network: everything runs on saved HTML fixtures.
"""

from __future__ import annotations

import court_monitor.services as services_module
from court_monitor.config.loader import SourceConfig
from court_monitor.domain.models import ParserStatus, SourceBackend, SourceType
from court_monitor.services import (
    ingest_fetch_result,
    parse_and_extract,
    process_pending,
    process_source,
    reprocess,
)
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
    article_facts = [f for f in relevant.facts if f.field == "criminal_article"]
    assert len(article_facts) >= 1
    art_val = article_facts[0].value
    assert isinstance(art_val, dict)
    assert art_val["article"] == "205.1"

    keyword_facts = [f for f in relevant.facts if f.field == "matched_keyword"]
    assert any("финансирование терроризма" in str(f.value) for f in keyword_facts)
    assert any(f.field == "title" for f in relevant.facts)
    assert any(f.field == "published_at" for f in relevant.facts)
    assert any(f.field == "full_name_original" for f in relevant.facts)

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


def test_ingest_fetch_result_scopes_external_id_by_source(db_session):
    """Telegram post ids are local to a channel; same id from another source is a distinct doc."""
    first = FetchResult.from_content(
        url="https://t.me/chan_a/42",
        canonical_url="https://t.me/chan_a/42",
        content="first channel post",
        text="first channel post",
        source_type=SourceType.telegram,
        source_name="Channel A",
        source_id="chan_a",
        external_id="42",
    )
    second = FetchResult.from_content(
        url="https://t.me/chan_b/42",
        canonical_url="https://t.me/chan_b/42",
        content="second channel post",
        text="second channel post",
        source_type=SourceType.telegram,
        source_name="Channel B",
        source_id="chan_b",
        external_id="42",
    )

    doc1, created1 = ingest_fetch_result(db_session, first)
    doc2, created2 = ingest_fetch_result(db_session, second)
    doc1_again, created1_again = ingest_fetch_result(db_session, first)

    assert created1 is True
    assert created2 is True
    assert created1_again is False
    assert doc1.id != doc2.id
    assert doc1_again.id == doc1.id


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
    article_facts = [f for f in relevant.facts if f.field == "criminal_article"]
    assert len(article_facts) >= 1
    assert isinstance(article_facts[0].value, dict)
    assert article_facts[0].value["article"] == "205.1"


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
    source = _make_source(str(fixtures_html_dir))
    process_source(db_session, source, monitoring_cfg, parse_immediately=False)

    stats1 = process_pending(db_session, monitoring_cfg)
    assert stats1.parsed + stats1.irrelevant == 2

    stats2 = process_pending(db_session, monitoring_cfg)
    assert stats2.parsed == 0
    assert stats2.irrelevant == 0
    assert stats2.failed == 0


def test_process_pending_drains_more_than_one_batch(db_session, monitoring_cfg, fixtures_html_dir):
    """Default pending batch size is 100; process_pending must loop until the queue is empty."""
    source = _make_source(str(fixtures_html_dir))
    sample = next(iter(SudrfAdapter(source).fetch_new()))

    for i in range(105):
        result = FetchResult.from_content(
            url=f"https://2zovs.sudrf.ru/fixture/relevant-{i}.html",
            content=sample.content,
            source_type=SourceType.sudrf,
            source_name=source.name,
        )
        doc, created = ingest_fetch_result(db_session, result)
        assert created is True
        assert doc.parser_status == ParserStatus.pending.value

    stats = process_pending(db_session, monitoring_cfg)

    assert stats.parsed + stats.irrelevant + stats.failed == 105
    assert repo.count_pending_documents(db_session) == 0


# ---------------------------------------------------------------------------
# Etap 3: extraction modules integration
# ---------------------------------------------------------------------------


def test_extraction_articles_in_pipeline(db_session, monitoring_cfg, fixtures_html_dir):
    """Articles extracted as structured dicts in pipeline."""
    source = _make_source(str(fixtures_html_dir))
    process_source(db_session, source, monitoring_cfg, parse_immediately=False)
    process_pending(db_session, monitoring_cfg)

    docs = repo.list_documents(db_session)
    relevant = next(d for d in docs if d.parser_status == ParserStatus.parsed.value)

    article_facts = [f for f in relevant.facts if f.field == "criminal_article"]
    assert len(article_facts) >= 1
    assert isinstance(article_facts[0].value, dict)
    assert "article" in article_facts[0].value


def test_extraction_dates_in_pipeline(db_session, monitoring_cfg, fixtures_html_dir):
    """Dates extracted with type and context."""
    source = _make_source(str(fixtures_html_dir))
    process_source(db_session, source, monitoring_cfg, parse_immediately=False)
    process_pending(db_session, monitoring_cfg)

    docs = repo.list_documents(db_session)
    relevant = next(d for d in docs if d.parser_status == ParserStatus.parsed.value)

    date_facts = [f for f in relevant.facts if f.field == "date"]
    assert len(date_facts) >= 1
    assert isinstance(date_facts[0].value, dict)
    assert "date" in date_facts[0].value


def test_extraction_names_in_pipeline(db_session, monitoring_cfg, fixtures_html_dir):
    """Name candidates extracted with confidence."""
    source = _make_source(str(fixtures_html_dir))
    process_source(db_session, source, monitoring_cfg, parse_immediately=False)
    process_pending(db_session, monitoring_cfg)

    docs = repo.list_documents(db_session)
    relevant = next(d for d in docs if d.parser_status == ParserStatus.parsed.value)

    name_facts = [f for f in relevant.facts if f.field == "full_name_original"]
    assert len(name_facts) >= 1
    assert name_facts[0].confidence > 0
    assert name_facts[0].quote


def test_extraction_no_duplicates_on_rerun(db_session, monitoring_cfg, fixtures_html_dir):
    """Re-running parse-pending does not create duplicate facts."""
    source = _make_source(str(fixtures_html_dir))
    process_source(db_session, source, monitoring_cfg, parse_immediately=False)
    process_pending(db_session, monitoring_cfg)

    docs = repo.list_documents(db_session)
    relevant = next(d for d in docs if d.parser_status == ParserStatus.parsed.value)
    facts_count = len(relevant.facts)

    # Second run should not create new facts (no pending docs)
    process_pending(db_session, monitoring_cfg)
    db_session.refresh(relevant)
    assert len(relevant.facts) == facts_count


# ---------------------------------------------------------------------------
# ReviewItem: parser failure creates an operator-facing review item
# ---------------------------------------------------------------------------


def test_parser_failure_creates_review_item(db_session, monitoring_cfg, monkeypatch):
    """A sudrf parser exception must set parser_failed AND leave a ReviewItem
    for an operator to triage — previously it only logged and vanished."""

    def _boom(_html: str):
        raise ValueError("selector engine exploded")

    monkeypatch.setattr(services_module, "parse_press_release", _boom)

    result = FetchResult.from_content(
        url="https://test/broken",
        content="<html><body>irrelevant</body></html>",
        source_type=SourceType.sudrf,
        source_name="test-court",
        source_id="test-court-id",
    )
    doc, created = ingest_fetch_result(db_session, result)
    assert created is True

    facts = parse_and_extract(db_session, doc, monitoring_cfg)
    assert facts == []
    assert doc.parser_status == ParserStatus.parser_failed.value

    items = repo.list_review_items(db_session, item_type="parser_failed")
    assert len(items) == 1
    item = items[0]
    assert item.document_id == doc.id
    assert item.source_id == "test-court-id"
    assert item.priority == "high"
    assert item.status == "pending"
    assert "selector engine exploded" in item.data_json


def test_reprocessing_a_persistently_broken_document_does_not_duplicate_review_items(
    db_session, monitoring_cfg, monkeypatch
):
    """reprocess-document on a document that keeps failing must refresh the
    existing pending ReviewItem, not create a new one on every attempt."""

    def _boom(_html: str):
        raise ValueError("still broken")

    monkeypatch.setattr(services_module, "parse_press_release", _boom)

    result = FetchResult.from_content(
        url="https://test/broken",
        content="<html><body>irrelevant</body></html>",
        source_type=SourceType.sudrf,
        source_name="test-court",
    )
    doc, _ = ingest_fetch_result(db_session, result)

    parse_and_extract(db_session, doc, monitoring_cfg)
    reprocess(db_session, doc.id)
    reprocess(db_session, doc.id)

    items = repo.list_review_items(db_session, item_type="parser_failed")
    assert len(items) == 1
    assert items[0].status == "pending"
