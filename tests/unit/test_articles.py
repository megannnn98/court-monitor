"""Unit tests: criminal article extraction."""

from __future__ import annotations

from court_monitor.extraction.articles import extract_article_strings, extract_articles


def test_explicit_article_with_uk_rf():
    dtos = extract_articles("осуждён по ч. 2 ст. 205.1 УК РФ к лишению свободы")
    values = [str(d.value) for d in dtos]
    assert "205.1" in values
    # explicit form => high confidence
    assert any(d.confidence >= 0.9 for d in dtos if str(d.value) == "205.1")
    # every fact carries a quote and a method
    assert all(d.quote for d in dtos)
    assert all(d.extraction_method.startswith("regex:article") for d in dtos)


def test_article_with_word_prefix_only():
    dtos = extract_articles("предусмотренного статьей 282")
    assert "282" in [str(d.value) for d in dtos]
    # prefix-only form is weaker than explicit-with-УК
    assert any(0.5 <= d.confidence < 0.95 for d in dtos)


def test_bare_number_near_uk():
    dtos = extract_articles("по статьям 205, 206 УК РФ были изменены")
    values = [str(d.value) for d in dtos]
    assert "205" in values


def test_no_false_positive_on_dates_or_plain_numbers():
    assert extract_article_strings("суд состоялся 02.04.2026, дело № 1-42/2026") == []


def test_deduped_unique_ordered():
    values = extract_article_strings("ст. 205.1 УК РФ; статья 205.1 упоминается снова")
    assert values.count("205.1") == 1
