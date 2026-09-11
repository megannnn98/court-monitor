from collections.abc import Sequence

from models import SearchHit


def reciprocal_rank_fusion(
    lexical_hits: Sequence[SearchHit],
    dense_hits: Sequence[SearchHit],
    *,
    limit: int,
    rrf_k: int = 60,
) -> list[SearchHit]:
    if rrf_k <= 0:
        raise ValueError("rrf_k must be greater than 0")

    if limit <= 0:
        raise ValueError("limit must be greater than 0")

    scores: dict[int, float] = {}
    hits_by_id: dict[int, SearchHit] = {}
    lexical_ranks: dict[int, int] = {}
    dense_ranks: dict[int, int] = {}

    for rank, hit in enumerate(lexical_hits, start=1):
        if hit.chunk_id in lexical_ranks:
            continue

        lexical_ranks[hit.chunk_id] = rank
        hits_by_id.setdefault(hit.chunk_id, hit)

        contribution = 1.0 / (rrf_k + rank)
        scores[hit.chunk_id] = scores.get(hit.chunk_id, 0.0) + contribution

    for rank, hit in enumerate(dense_hits, start=1):
        if hit.chunk_id in dense_ranks:
            continue

        dense_ranks[hit.chunk_id] = rank
        hits_by_id.setdefault(hit.chunk_id, hit)

        contribution = 1.0 / (rrf_k + rank)
        scores[hit.chunk_id] = scores.get(hit.chunk_id, 0.0) + contribution

    missing_rank = max(len(lexical_hits), len(dense_hits)) + 1

    def sort_key(
        chunk_id: int,
    ) -> tuple[float, int, int, int, int]:
        lexical_rank = lexical_ranks.get(chunk_id, missing_rank)
        dense_rank = dense_ranks.get(chunk_id, missing_rank)
        best_rank = min(lexical_rank, dense_rank)

        return (
            -scores[chunk_id],
            best_rank,
            lexical_rank,
            dense_rank,
            chunk_id,
        )

    sorted_chunk_ids = sorted(scores, key=sort_key)[:limit]

    return [
        hits_by_id[chunk_id].model_copy(update={"score": scores[chunk_id]})
        for chunk_id in sorted_chunk_ids
    ]
