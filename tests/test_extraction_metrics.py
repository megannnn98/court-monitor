from pathlib import Path

from extraction_metrics import _safe_div, evaluate_golden_dataset, load_golden_articles

GOLDEN_PATH = Path("tests/fixtures/extraction_golden_corpus.json")


def test_load_golden_articles_populates_offsets() -> None:
    articles = load_golden_articles(GOLDEN_PATH)

    assert len(articles) == 10
    for article in articles:
        for mention in article.mentions:
            assert mention.start_offset is not None
            assert mention.end_offset is not None
            assert article.text[mention.start_offset : mention.end_offset] == mention.surface_text


def test_evaluate_golden_dataset_returns_error_breakdown() -> None:
    report = evaluate_golden_dataset(GOLDEN_PATH)

    assert 0 <= report.exact_span_precision <= 1
    assert 0 <= report.exact_span_recall <= 1
    assert isinstance(report.false_positive_mentions, list)
    assert isinstance(report.missed_mentions, list)


def test_safe_div_returns_zero_for_empty_denominator() -> None:
    assert _safe_div(0, 0) == 0.0
