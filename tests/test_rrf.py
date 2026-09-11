import pytest

from models import SearchHit
from rrf import reciprocal_rank_fusion


def make_hit(chunk_id: int, *, score: float = 1.0) -> SearchHit:
    return SearchHit(
        chunk_id=chunk_id,
        article_id=chunk_id,
        source_base_url="https://example.com",
        external_id=f"article-{chunk_id}",
        ordinal=0,
        title=f"Article {chunk_id}",
        published_at=None,
        url=f"https://example.com/{chunk_id}",
        text=f"Chunk {chunk_id}",
        score=score,
    )


def test_fuses_lexical_and_dense_results() -> None:
    lexical_hits = [
        make_hit(1),
        make_hit(2),
        make_hit(3),
    ]
    dense_hits = [
        make_hit(3),
        make_hit(1),
        make_hit(4),
    ]

    result = reciprocal_rank_fusion(
        lexical_hits,
        dense_hits,
        limit=10,
        rrf_k=10,
    )

    assert [hit.chunk_id for hit in result] == [1, 3, 2, 4]

    scores = {hit.chunk_id: hit.score for hit in result}

    assert scores[1] == pytest.approx(1 / 11 + 1 / 12)
    assert scores[2] == pytest.approx(1 / 12)
    assert scores[3] == pytest.approx(1 / 13 + 1 / 11)
    assert scores[4] == pytest.approx(1 / 13)


def test_same_chunk_is_not_duplicated() -> None:
    lexical_hit = make_hit(1, score=0.9)
    dense_hit = make_hit(1, score=0.8)

    result = reciprocal_rank_fusion(
        [lexical_hit],
        [dense_hit],
        limit=10,
        rrf_k=10,
    )

    assert len(result) == 1
    assert result[0].chunk_id == 1
    assert result[0].score == pytest.approx(2 / 11)


def test_original_scores_do_not_affect_order() -> None:
    lexical_hits = [
        make_hit(1, score=0.01),
        make_hit(2, score=1000.0),
    ]

    result = reciprocal_rank_fusion(
        lexical_hits,
        [],
        limit=10,
        rrf_k=10,
    )

    assert [hit.chunk_id for hit in result] == [1, 2]


def test_empty_results_return_empty_list() -> None:
    result = reciprocal_rank_fusion([], [], limit=10)

    assert result == []


def test_non_positive_rrf_k_is_rejected() -> None:
    with pytest.raises(
        ValueError,
        match="rrf_k must be greater than 0",
    ):
        reciprocal_rank_fusion([], [], limit=10, rrf_k=0)


def test_equal_scores_are_resolved_by_lexical_rank() -> None:
    lexical_hits = [
        make_hit(1),
        make_hit(2),
        make_hit(3),
    ]
    dense_hits = [
        make_hit(3),
        make_hit(4),
        make_hit(1),
    ]

    result = reciprocal_rank_fusion(
        lexical_hits,
        dense_hits,
        limit=4,
        rrf_k=10,
    )

    assert result[0].score == pytest.approx(result[1].score)
    assert [hit.chunk_id for hit in result] == [1, 3, 2, 4]
