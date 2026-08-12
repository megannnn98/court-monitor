"""Tests for article selection and candidate gating in court orchestrator."""

from __future__ import annotations

from court_monitor.config.loader import MonitoringConfig
from court_monitor.domain.facts import ExtractedFactDTO
from court_monitor.domain.models import VerificationStatus
from court_monitor.matching.case_matching import MatchSignal
from court_monitor.services.court_orchestrator import (
    MEANINGFUL_CASE_SIGNALS,
    PipelineOutcome,
    _has_meaningful_signal,
    select_search_articles,
)


def _make_article_fact(article: str) -> ExtractedFactDTO:
    return ExtractedFactDTO(
        entity="case",
        field="criminal_article",
        value={"article": article},
        verification_status=VerificationStatus.inferred,
        confidence=0.9,
        quote=f"ст. {article}",
        extraction_method="regex",
    )


def _monitoring(*articles: str) -> MonitoringConfig:
    return MonitoringConfig(criminal_articles=list(articles))


# ── select_search_articles ────────────────────────────────────────────


def test_select_search_articles_filters_unmonitored():
    """Extracted ["354.1", "205.2"] with monitoring=["205.2"] → ["205.2"]."""
    facts = [_make_article_fact("354.1"), _make_article_fact("205.2")]
    monitoring = _monitoring("205.2")
    selected, method = select_search_articles(730, facts, monitoring)

    assert selected == ["205.2"]
    assert method == "monitoring_relevance"


def test_select_search_articles_multiple_monitored():
    """Both monitored articles are retained in insertion order."""
    facts = [_make_article_fact("205.1"), _make_article_fact("205.2")]
    monitoring = _monitoring("205.1", "205.2")
    selected, method = select_search_articles(1, facts, monitoring)

    assert selected == ["205.1", "205.2"]
    assert method == "monitoring_relevance"


def test_select_search_articles_deduplicates():
    """Same article appearing twice → returned once."""
    facts = [_make_article_fact("205.2"), _make_article_fact("205.2")]
    monitoring = _monitoring("205.2")
    selected, method = select_search_articles(1, facts, monitoring)

    assert selected == ["205.2"]


def test_select_search_articles_fallback_when_none_matched():
    """No article matches monitoring → fallback to all extracted."""
    facts = [_make_article_fact("354.1")]
    monitoring = _monitoring("205.2")
    selected, method = select_search_articles(1, facts, monitoring)

    assert selected == ["354.1"]
    assert method == "all_extracted_fallback"


def test_select_search_articles_parent_article_match():
    """Extracted "205.1" matches monitored "205" (parent)."""
    facts = [_make_article_fact("205.1")]
    monitoring = _monitoring("205")
    selected, method = select_search_articles(1, facts, monitoring)

    assert selected == ["205.1"]
    assert method == "monitoring_relevance"


def test_select_search_articles_empty_extraction():
    """No articles extracted → empty list."""
    selected, method = select_search_articles(1, [], _monitoring("205.2"))
    assert selected == []
    assert method == "all_extracted_fallback"


# ── _has_meaningful_signal ────────────────────────────────────────────


def _signal(signal_type: str, weight: float = 0.2) -> MatchSignal:
    return MatchSignal(
        signal_type=signal_type,
        description=f"test {signal_type}",
        criteria_value="x",
        case_value="y",
        weight=weight,
    )


def test_meaningful_signal_article_is_meaningful():
    assert _has_meaningful_signal([_signal("article")]) is True


def test_meaningful_signal_date_is_meaningful():
    assert _has_meaningful_signal([_signal("date")]) is True


def test_meaningful_signal_person_is_meaningful():
    assert _has_meaningful_signal([_signal("person_name")]) is True


def test_meaningful_signal_court_only_not_meaningful():
    assert _has_meaningful_signal([_signal("court")]) is False


def test_meaningful_signal_court_plus_article_is_meaningful():
    assert _has_meaningful_signal([_signal("court"), _signal("article")]) is True


def test_meaningful_signal_empty_signals():
    assert _has_meaningful_signal([]) is False


# ── MEANINGFUL_CASE_SIGNALS definition ────────────────────────────────


def test_court_is_excluded_from_meaningful():
    assert "court" not in MEANINGFUL_CASE_SIGNALS


def test_article_date_person_are_meaningful():
    assert "article" in MEANINGFUL_CASE_SIGNALS
    assert "date" in MEANINGFUL_CASE_SIGNALS
    assert "person_name" in MEANINGFUL_CASE_SIGNALS


# ── PipelineOutcome ───────────────────────────────────────────────────


def test_outcome_defaults():
    o = PipelineOutcome()
    assert o.weak_candidates_skipped == 0
    assert o.matched_cases == []
    assert o.search_succeeded is False
