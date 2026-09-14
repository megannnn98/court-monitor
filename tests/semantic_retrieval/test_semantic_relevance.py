"""Semantic relevance acceptance: nearest neighbours are not automatically relevant."""

from __future__ import annotations

import logging

import pytest

from semantic_retrieval.models import (
    RetrievalBackend,
    RetrievalEntityType,
    RetrievalHit,
    RetrievalQuery,
    RetrievalResult,
    SemanticConfigurationError,
)
from semantic_retrieval.relevance import (
    CALIBRATED_EMBEDDING_MODEL_ID,
    DEFAULT_DENSE_MIN_SCORE,
    DenseSimilarityRelevancePolicy,
    resolve_dense_min_score,
)
from semantic_retrieval.reranking import RetrievalCandidate, order_by_scores
from semantic_retrieval.rrf import reciprocal_rank_fusion

PERSON = RetrievalEntityType.PERSON
DENSE = RetrievalBackend.DENSE
LEXICAL = RetrievalBackend.LEXICAL
QUERY = RetrievalQuery(text="антивоенная позиция", entity_type=PERSON, limit=100)
POLICY = DenseSimilarityRelevancePolicy(dense_min_score=0.80)


def _hits(backend: RetrievalBackend, scored: list[tuple[int, float]]) -> list[RetrievalHit]:
    return [
        RetrievalHit(
            entity_type=PERSON, entity_id=entity_id, score=score, backend=backend, rank=rank
        )
        for rank, (entity_id, score) in enumerate(scored, start=1)
    ]


def _result(backend: RetrievalBackend, hits: list[RetrievalHit]) -> RetrievalResult:
    return RetrievalResult(entity_type=PERSON, backend=backend, hits=hits)


def test_a_strong_dense_match_is_accepted() -> None:
    decision = POLICY.accept(QUERY, _result(DENSE, _hits(DENSE, [(1, 0.84)])))

    assert decision.accepted.entity_ids == [1]
    assert decision.rejected_count == 0


def test_b_weak_nearest_neighbour_is_rejected() -> None:
    decision = POLICY.accept(QUERY, _result(DENSE, _hits(DENSE, [(1, 0.79)])))

    assert decision.accepted.entity_ids == []
    assert decision.rejected_count == 1


def test_c_unrelated_query_accepts_nothing_although_neighbours_exist() -> None:
    retrieved = _result(DENSE, _hits(DENSE, [(i, 0.74 - i / 1000) for i in range(1, 19)]))

    decision = POLICY.accept(QUERY, retrieved)

    assert decision.accepted.hits == []
    assert decision.retrieved == retrieved  # diagnostics keep what was filtered
    assert decision.rejected_count == 18


def test_d_one_strong_among_many_weak_keeps_only_the_strong_one_with_new_rank() -> None:
    retrieved = _result(DENSE, _hits(DENSE, [(5, 0.79), (7, 0.83), (9, 0.77), (2, 0.78)]))

    decision = POLICY.accept(QUERY, retrieved)

    (hit,) = decision.accepted.hits
    assert (hit.entity_id, hit.rank) == (7, 1)
    assert hit.component_ranks["dense"] == 2  # original position is preserved
    assert decision.accepted.backend is DENSE


def test_e_empty_retrieval_is_an_empty_decision() -> None:
    decision = POLICY.accept(QUERY, _result(DENSE, []))

    assert (decision.accepted.hits, decision.rejected_count) == ([], 0)


def test_threshold_is_inclusive() -> None:
    assert POLICY.accept(QUERY, _result(DENSE, _hits(DENSE, [(1, 0.80)]))).accepted.entity_ids == [
        1
    ]


def test_hybrid_lexical_rank_one_with_low_dense_similarity_is_not_relevant() -> None:
    fused = reciprocal_rank_fusion(
        {
            LEXICAL: _hits(LEXICAL, [(1, 0.9)]),
            DENSE: _hits(DENSE, [(2, 0.83), (3, 0.81), (1, 0.70)]),
        },
        limit=10,
    )
    assert fused[0].entity_id == 1  # RRF ranks the lexical + weak dense hit first

    decision = POLICY.accept(QUERY, _result(RetrievalBackend.HYBRID, fused))

    assert decision.accepted.entity_ids == [2, 3]
    assert 1 not in decision.accepted.entity_ids


def test_hybrid_lexical_only_hit_is_never_accepted() -> None:
    fused = reciprocal_rank_fusion({LEXICAL: _hits(LEXICAL, [(4, 1.0)]), DENSE: []}, limit=10)

    assert POLICY.accept(QUERY, _result(RetrievalBackend.HYBRID, fused)).accepted.hits == []


def test_rrf_keeps_component_scores_for_acceptance() -> None:
    fused = reciprocal_rank_fusion(
        {LEXICAL: _hits(LEXICAL, [(1, 0.4)]), DENSE: _hits(DENSE, [(1, 0.82)])}, limit=10
    )

    assert fused[0].component_scores == {"lexical": 0.4, "dense": 0.82}


def test_reranked_hits_are_accepted_by_their_dense_similarity_not_reranker_score() -> None:
    fused = reciprocal_rank_fusion({DENSE: _hits(DENSE, [(1, 0.70), (2, 0.85)])}, limit=10)
    reranked = order_by_scores(
        [RetrievalCandidate(hit, "text") for hit in fused], [9.0, -3.0], limit=2
    )
    assert reranked[0].entity_id == 1  # the reranker loves the weak one

    decision = POLICY.accept(QUERY, _result(RetrievalBackend.HYBRID_RERANKED, reranked))

    assert decision.accepted.entity_ids == [2]


def test_lexical_backend_alone_never_confirms_a_semantic_criterion() -> None:
    decision = POLICY.accept(QUERY, _result(LEXICAL, _hits(LEXICAL, [(1, 0.99)])))

    assert decision.accepted.hits == []


def test_acceptance_is_logged_with_counts_and_threshold_only(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="semantic_retrieval")

    POLICY.accept(QUERY, _result(DENSE, _hits(DENSE, [(1, 0.9), (2, 0.1)])))

    messages = [record.getMessage() for record in caplog.records]
    assert any(
        "semantic_acceptance retrieved=2 accepted=1 rejected=1 dense_min_score=0.800" in m
        for m in messages
    )
    assert not any("антивоенная" in m for m in messages)


# --- threshold configuration -------------------------------------------------------


def test_default_threshold_applies_only_to_the_calibrated_model() -> None:
    assert resolve_dense_min_score({}, CALIBRATED_EMBEDDING_MODEL_ID) == DEFAULT_DENSE_MIN_SCORE
    assert DEFAULT_DENSE_MIN_SCORE == 0.80


def test_other_model_requires_an_explicit_threshold() -> None:
    with pytest.raises(SemanticConfigurationError, match="SEMANTIC_DENSE_MIN_SCORE"):
        resolve_dense_min_score({}, "sentence-transformers/LaBSE")

    assert resolve_dense_min_score(
        {"SEMANTIC_DENSE_MIN_SCORE": "0.55"}, "sentence-transformers/LaBSE"
    ) == pytest.approx(0.55)


@pytest.mark.parametrize("value", ["abc", "1.5", "-1.1", "nan"])
def test_invalid_threshold_is_a_configuration_error(value: str) -> None:
    with pytest.raises(SemanticConfigurationError):
        resolve_dense_min_score({"SEMANTIC_DENSE_MIN_SCORE": value}, CALIBRATED_EMBEDDING_MODEL_ID)
