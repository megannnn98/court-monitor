import pytest

from evaluation_metrics import mean_reciprocal_rank, reciprocal_rank
from evaluation_models import ArticleReference


# быстро создаёт ArticleReference, чтобы не повторять один и тот же код в каждом тесте.
def make_article(external_id: str) -> ArticleReference:
    return ArticleReference(
        source_base_url="https://ovd.info",
        external_id=external_id,
    )


def test_reciprocal_rank_when_expected_article_is_first() -> None:
    expected = make_article("article-1")
    retrieved = [
        expected,
        make_article("article-2"),
        make_article("weather"),
    ]

    assert reciprocal_rank(retrieved, expected) == 1.0


def test_reciprocal_rank_uses_expected_article_position() -> None:
    expected = make_article("article-1")
    retrieved = [
        make_article("weather"),
        make_article("article-2"),
        expected,
    ]

    result = reciprocal_rank(retrieved, expected)

    assert result == pytest.approx(1 / 3)


def test_reciprocal_rank_is_zero_when_expected_article_is_missing() -> None:
    expected = make_article("article-1")
    retrieved = [
        make_article("article-2"),
        make_article("weather"),
    ]

    assert reciprocal_rank(retrieved, expected) == 0.0


def test_mean_reciprocal_rank() -> None:
    reciprocal_ranks = [
        1.0,
        0.5,
        0.0,
    ]

    result = mean_reciprocal_rank(reciprocal_ranks)

    assert result == pytest.approx(0.5)


def test_mean_reciprocal_rank_is_zero_for_empty_input() -> None:
    assert mean_reciprocal_rank([]) == 0.0
