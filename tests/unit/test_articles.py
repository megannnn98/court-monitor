"""Unit tests: criminal article extraction."""

from __future__ import annotations

from court_monitor.extraction.articles import extract_article_strings, extract_articles


def test_explicit_article_with_uk_rf():
    dtos = extract_articles("осуждён по ч. 2 ст. 205.1 УК РФ к лишению свободы")
    assert len(dtos) >= 1
    art = dtos[0].value
    assert isinstance(art, dict)
    assert art["article"] == "205.1"
    assert art.get("part") == "2"
    assert art.get("code") == "УК РФ"
    # explicit form => high confidence
    assert dtos[0].confidence >= 0.9
    assert dtos[0].quote
    assert dtos[0].extraction_method.startswith("regex:article")


def test_article_with_word_prefix_only():
    dtos = extract_articles("предусмотренного статьей 282")
    assert len(dtos) >= 1
    assert dtos[0].value["article"] == "282"
    assert 0.5 <= dtos[0].confidence < 0.95


def test_bare_number_near_uk():
    dtos = extract_articles("по статьям 205, 206 УК РФ были изменены")
    articles = [d.value["article"] for d in dtos]
    assert "205" in articles


def test_no_false_positive_on_dates_or_plain_numbers():
    assert extract_article_strings("суд состоялся 02.04.2026, дело № 1-42/2026") == []


def test_deduped_unique_ordered():
    values = extract_article_strings("ст. 205.1 УК РФ; статья 205.1 упоминается снова")
    assert values.count("205.1") == 1


def test_part_and_point_extraction():
    dtos = extract_articles("п. «а» ч. 2 ст. 205 УК РФ")
    assert len(dtos) >= 1
    art = dtos[0].value
    assert art["article"] == "205"
    assert art["part"] == "2"
    assert art["point"] == "а"
    assert art["code"] == "УК РФ"
    assert dtos[0].confidence >= 0.95


def test_part_article_without_point():
    dtos = extract_articles("ч. 3 ст. 228 УК РФ")
    assert len(dtos) >= 1
    art = dtos[0].value
    assert art["article"] == "228"
    assert art["part"] == "3"
    assert "point" not in art


def test_multiple_articles_in_text():
    text = "По ст. 205 УК РФ и ст. 282 УК РФ вынесено решение"
    dtos = extract_articles(text)
    articles = [d.value["article"] for d in dtos]
    assert "205" in articles
    assert "282" in articles


def test_full_structured_has_highest_confidence():
    dtos = extract_articles("п. «б» ч. 1 ст. 105 УК РФ")
    assert len(dtos) >= 1
    assert dtos[0].confidence >= 0.95


def test_compound_charge_keeps_the_article_actually_charged():
    """Regression, from document 56 of the live corpus.

    "ч. 1 ст. 30, ч. 2 ст. 205.5 УК РФ" is the standard formula for preparing
    a terrorist offence. The span opened at ст. 30 used to consume everything
    up to УК, and because later passes skip claimed spans, 205.5 was never
    extracted — the relevance filter then dropped the document, since ст. 30
    alone is in nobody's watch list.
    """
    text = "Подростка задержали, возбуждено уголовное дело по ч. 1 ст. 30, ч. 2 ст. 205.5 УК РФ"

    articles = extract_article_strings(text)

    assert articles == ["30", "205.5"]


def test_compound_charge_with_three_references():
    """Complicity + preparation + the offence itself, all in one citation."""
    articles = extract_article_strings("ч. 1 ст. 33, ч. 3 ст. 30, ч. 2 ст. 205 УК РФ")

    assert articles == ["33", "30", "205"]


def test_compound_charge_keeps_parts_with_their_own_articles():
    """Each reference must keep its own часть, not inherit a neighbour's."""
    dtos = extract_articles("ч. 1 ст. 30, ч. 2 ст. 205.5 УК РФ")

    by_article = {d.value["article"]: d.value.get("part") for d in dtos}
    assert by_article == {"30": "1", "205.5": "2"}
