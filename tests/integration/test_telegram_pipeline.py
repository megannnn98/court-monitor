"""Integration test: Telegram registry source → fetch → parse → extract.

Regression coverage for a bug where ``parse_and_extract`` unconditionally ran
the sudrf structural HTML parser on non-sudrf documents, polluting the
extraction text with Telegram UI chrome (channel/author line, view counters,
"VIEW IN TELEGRAM" labels) instead of using the already-clean text produced
by the Telegram adapter.
"""

from __future__ import annotations

from pathlib import Path

from court_monitor.config.registry import SourceRegistryEntry
from court_monitor.domain.models import ParserStatus
from court_monitor.services import process_registry_source
from court_monitor.storage import repository as repo

FIXTURE = (
    Path(__file__).resolve().parent.parent
    / "fixtures"
    / "telegram"
    / "tg_preview_extremizmunet.html"
)


def _entry() -> SourceRegistryEntry:
    return SourceRegistryEntry(
        id="extremizmunet",
        name="Экстремизму - НЕТ!",
        url="https://t.me/extremizmunet",
        domain="t.me",
        source_type="telegram",
        username="extremizmunet",
    )


def test_telegram_registry_source_ingests_and_parses(db_session, monitoring_cfg):
    stats = process_registry_source(db_session, _entry(), monitoring_cfg, fixture_path=str(FIXTURE))

    assert stats.fetched >= 10
    assert stats.new_documents == stats.fetched
    assert stats.failed == 0

    docs = repo.list_documents(db_session)
    assert len(docs) == stats.fetched
    assert all(d.source_type == "telegram" for d in docs)


def test_telegram_post_relevance_and_article_extraction(db_session, monitoring_cfg):
    """Post #3998 explicitly mentions 'ст. 205.1 УК РФ' and 'финансирование
    терроризма' — both configured in monitoring_cfg — so it must be flagged
    relevant with the article extracted."""
    process_registry_source(db_session, _entry(), monitoring_cfg, fixture_path=str(FIXTURE))

    docs = repo.list_documents(db_session)
    post = next(d for d in docs if d.external_id == "3998")

    assert post.parser_status == ParserStatus.parsed.value
    assert post.relevant

    article_facts = [f for f in post.facts if f.field == "criminal_article"]
    assert any(f.value.get("article") == "205.1" for f in article_facts)

    keyword_facts = [f for f in post.facts if f.field == "matched_keyword"]
    assert any("финансирование терроризма" in str(f.value) for f in keyword_facts)


def test_telegram_extraction_text_excludes_ui_chrome(db_session, monitoring_cfg):
    """Regression: extraction facts must be sourced from the clean adapter
    text, not from re-running the sudrf HTML parser (which would prepend the
    channel/author line and strings like 'VIEW IN TELEGRAM')."""
    process_registry_source(db_session, _entry(), monitoring_cfg, fixture_path=str(FIXTURE))

    docs = repo.list_documents(db_session)
    post = next(d for d in docs if d.external_id == "3998")

    assert post.text is not None
    assert not post.text.startswith("Экстремизму")
    assert "VIEW IN TELEGRAM" not in post.text

    for fact in post.facts:
        if fact.quote:
            assert "VIEW IN TELEGRAM" not in fact.quote
            assert "Media is too big" not in fact.quote

    # sudrf-specific title/date facts only make sense for the sudrf parser —
    # a non-HTML-document source must never emit them.
    assert not any(
        f.field in ("title", "published_at")
        and f.extraction_method.startswith("parser:sudrf_press")
        for f in post.facts
    )


def test_telegram_no_duplicates_on_rerun(db_session, monitoring_cfg):
    first = process_registry_source(db_session, _entry(), monitoring_cfg, fixture_path=str(FIXTURE))
    second = process_registry_source(
        db_session, _entry(), monitoring_cfg, fixture_path=str(FIXTURE)
    )

    assert second.new_documents == 0
    assert second.duplicates == first.fetched
    assert repo.count_documents(db_session) == first.fetched
