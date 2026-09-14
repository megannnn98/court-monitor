"""Reciprocal Rank Fusion keyed by (entity_type, entity_id).

Ported from the removed chunk-level `rrf.py` (ADR 0002): same formula and
tie-breaking, generalized to any number of ranked lists.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from semantic_retrieval.models import RetrievalBackend, RetrievalEntityType, RetrievalHit

DEFAULT_RRF_K = 60

type _EntityKey = tuple[RetrievalEntityType, int]


def reciprocal_rank_fusion(
    ranked_lists: Mapping[RetrievalBackend, Sequence[RetrievalHit]],
    *,
    limit: int,
    rrf_k: int = DEFAULT_RRF_K,
    backend: RetrievalBackend = RetrievalBackend.HYBRID,
) -> list[RetrievalHit]:
    """score(entity) = Σ over lists of 1 / (rrf_k + rank in that list).

    Input scores are ignored; only positions count. Duplicates inside a list
    keep their first position. Ties: higher score, better best rank, better
    rank in earlier lists (mapping order), entity type, entity id.
    """
    if rrf_k <= 0:
        raise ValueError("rrf_k must be greater than 0")
    if limit <= 0:
        raise ValueError("limit must be greater than 0")

    scores: dict[_EntityKey, float] = {}
    ranks: dict[RetrievalBackend, dict[_EntityKey, int]] = {}
    raw_scores: dict[RetrievalBackend, dict[_EntityKey, float]] = {}
    for list_backend, hits in ranked_lists.items():
        list_ranks = ranks.setdefault(list_backend, {})
        list_scores = raw_scores.setdefault(list_backend, {})
        position = 0
        for hit in hits:
            key = (hit.entity_type, hit.entity_id)
            if key in list_ranks:
                continue
            position += 1
            list_ranks[key] = position
            list_scores[key] = hit.score
            scores[key] = scores.get(key, 0.0) + 1.0 / (rrf_k + position)

    missing_rank = max((len(hits) for hits in ranked_lists.values()), default=0) + 1

    def sort_key(key: _EntityKey) -> tuple[float, int, tuple[int, ...], str, int]:
        per_list = tuple(list_ranks.get(key, missing_rank) for list_ranks in ranks.values())
        return (-scores[key], min(per_list), per_list, key[0].value, key[1])

    fused: list[RetrievalHit] = []
    for rank, key in enumerate(sorted(scores, key=sort_key)[:limit], start=1):
        fused.append(
            RetrievalHit(
                entity_type=key[0],
                entity_id=key[1],
                score=scores[key],
                backend=backend,
                rank=rank,
                component_ranks={
                    list_backend.value: list_ranks[key]
                    for list_backend, list_ranks in ranks.items()
                    if key in list_ranks
                },
                component_scores={
                    list_backend.value: list_scores[key]
                    for list_backend, list_scores in raw_scores.items()
                    if key in list_scores
                },
            )
        )
    return fused
