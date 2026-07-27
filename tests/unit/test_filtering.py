"""Unit tests: relevance filtering."""

from __future__ import annotations

from court_monitor.config.loader import MonitoringConfig
from court_monitor.extraction.filtering import evaluate_relevance


def test_relevant_by_explicit_article():
    cfg = MonitoringConfig(criminal_articles=["205.1"], keywords=[])
    rel = evaluate_relevance("осуждён по ст. 205.1 УК РФ", cfg)
    assert rel.relevant
    assert "205.1" in rel.matched_articles


def test_relevant_by_parent_article_match():
    # monitored "205" should also catch "205.1"
    cfg = MonitoringConfig(criminal_articles=["205"], keywords=[])
    rel = evaluate_relevance("по статье 205.1 УК РФ назначено наказание", cfg)
    assert rel.relevant


def test_relevant_by_keyword_only():
    cfg = MonitoringConfig(criminal_articles=[], keywords=["финансирование терроризма"])
    rel = evaluate_relevance("суд установил факт финансирование терроризма в косвенной форме", cfg)
    # NB: keyword stored lowercased; "финансирование" substring matches.
    assert rel.relevant


def test_irrelevant_when_no_article_and_no_keyword():
    cfg = MonitoringConfig(criminal_articles=["205.1"], keywords=["диверсия"])
    rel = evaluate_relevance("штраф за нарушение ПДД, дело № 5-1/2026", cfg)
    assert not rel.relevant
    assert rel.matched_articles == []
    assert rel.matched_keywords == []
