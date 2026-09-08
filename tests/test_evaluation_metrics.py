import pytest

from evaluation_metrics import mean_reciprocal_rank, reciprocal_rank
from evaluation_models import ChunkReference


# быстро создаёт ChunkReference, чтобы не повторять один и тот же код в каждом тесте.
def make_chunk(external_id: str, ordinal: int = 0) -> ChunkReference:
    return ChunkReference(
        source_base_url="https://ovd.info",
        external_id=external_id,
        ordinal=ordinal,
    )


def test_reciprocal_rank_when_expected_chunk_is_first() -> None:
    expected = make_chunk("article-1")
    retrieved = [
        expected,
        make_chunk("article-2"),
        make_chunk("weather"),
    ]

    assert reciprocal_rank(retrieved, expected) == 1.0


def test_reciprocal_rank_uses_expected_chunk_position() -> None:
    expected = make_chunk("article-1", ordinal=1)
    retrieved = [
        make_chunk("weather"),
        make_chunk("article-2"),
        expected,
    ]

    result = reciprocal_rank(retrieved, expected)

    assert result == pytest.approx(1 / 3)


def test_reciprocal_rank_is_zero_when_expected_chunk_is_missing() -> None:
    expected = make_chunk("article-1")
    retrieved = [
        make_chunk("article-2"),
        make_chunk("weather"),
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
