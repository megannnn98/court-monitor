from collections.abc import Sequence

from evaluation_models import ChunkReference


# на каком месте поисковой выдачи находится правильный чанк
def reciprocal_rank(
    retrieved: Sequence[ChunkReference],
    expected_chunk: ChunkReference,
) -> float:
    for rank, chunk in enumerate(retrieved, start=1):
        if chunk == expected_chunk:
            return 1.0 / rank

    return 0.0


# считает среднее качество поиска по всем проверочным запросам
def mean_reciprocal_rank(reciprocal_ranks: Sequence[float]) -> float:
    if not reciprocal_ranks:
        return 0.0

    return sum(reciprocal_ranks) / len(reciprocal_ranks)
