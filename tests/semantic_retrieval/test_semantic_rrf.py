"""Reciprocal Rank Fusion over entity hits (ported from the chunk-level RRF)."""

from __future__ import annotations

import pytest

from semantic_retrieval.models import RetrievalBackend, RetrievalEntityType, RetrievalHit
from semantic_retrieval.rrf import reciprocal_rank_fusion

PERSON = RetrievalEntityType.PERSON


def _hits(
    backend: RetrievalBackend, entity_ids: list[int], *, entity_type: RetrievalEntityType = PERSON
) -> list[RetrievalHit]:
    return [
        RetrievalHit(
            entity_type=entity_type,
            entity_id=entity_id,
            score=100.0 - rank,
            backend=backend,
            rank=rank,
        )
        for rank, entity_id in enumerate(entity_ids, start=1)
    ]


LEXICAL = RetrievalBackend.LEXICAL
DENSE = RetrievalBackend.DENSE


def test_fuses_lists_with_rrf_scores_and_ranks() -> None:
    fused = reciprocal_rank_fusion(
        {LEXICAL: _hits(LEXICAL, [1, 2, 3]), DENSE: _hits(DENSE, [3, 1, 4])},
        limit=10,
        rrf_k=10,
    )

    assert [hit.entity_id for hit in fused] == [1, 3, 2, 4]
    assert [hit.rank for hit in fused] == [1, 2, 3, 4]
    assert {hit.backend for hit in fused} == {RetrievalBackend.HYBRID}
    scores = {hit.entity_id: hit.score for hit in fused}
    assert scores[1] == pytest.approx(1 / 11 + 1 / 12)
    assert scores[3] == pytest.approx(1 / 13 + 1 / 11)
    assert scores[2] == pytest.approx(1 / 12)
    assert scores[4] == pytest.approx(1 / 13)
    assert fused[0].component_ranks == {"lexical": 1, "dense": 2}
    assert fused[3].component_ranks == {"dense": 3}


def test_lexical_only_and_dense_only_hits_are_kept() -> None:
    fused = reciprocal_rank_fusion(
        {LEXICAL: _hits(LEXICAL, [7]), DENSE: _hits(DENSE, [8])}, limit=10
    )

    assert {hit.entity_id for hit in fused} == {7, 8}


def test_same_entity_from_both_retrievers_is_deduplicated() -> None:
    fused = reciprocal_rank_fusion(
        {LEXICAL: _hits(LEXICAL, [5, 5]), DENSE: _hits(DENSE, [5])}, limit=10, rrf_k=10
    )

    (hit,) = fused
    assert hit.score == pytest.approx(2 / 11)


def test_same_id_of_different_entity_types_is_not_merged() -> None:
    fused = reciprocal_rank_fusion(
        {
            LEXICAL: _hits(LEXICAL, [1]),
            DENSE: _hits(DENSE, [1], entity_type=RetrievalEntityType.EVENT),
        },
        limit=10,
    )

    assert sorted((hit.entity_type.value, hit.entity_id) for hit in fused) == [
        ("event", 1),
        ("person", 1),
    ]


def test_ties_are_broken_deterministically_by_best_rank_then_list_order_then_id() -> None:
    # 10 and 20 both appear once at rank 1: equal RRF score and best rank.
    # The list given first (lexical) wins; then the lower entity id.
    fused = reciprocal_rank_fusion(
        {LEXICAL: _hits(LEXICAL, [20, 31]), DENSE: _hits(DENSE, [10, 30])}, limit=10
    )

    assert [hit.entity_id for hit in fused] == [20, 10, 31, 30]
    for _ in range(3):
        again = reciprocal_rank_fusion(
            {LEXICAL: _hits(LEXICAL, [20, 31]), DENSE: _hits(DENSE, [10, 30])}, limit=10
        )
        assert [hit.entity_id for hit in again] == [20, 10, 31, 30]


def test_input_scores_do_not_affect_order_and_limit_applies() -> None:
    lexical = _hits(LEXICAL, [1, 2, 3])
    lexical[2] = lexical[2].model_copy(update={"score": 1e9})

    fused = reciprocal_rank_fusion({LEXICAL: lexical}, limit=2)

    assert [hit.entity_id for hit in fused] == [1, 2]


@pytest.mark.parametrize(("limit", "rrf_k"), [(0, 60), (10, 0)])
def test_invalid_parameters_are_rejected(limit: int, rrf_k: int) -> None:
    with pytest.raises(ValueError):
        reciprocal_rank_fusion({LEXICAL: []}, limit=limit, rrf_k=rrf_k)
