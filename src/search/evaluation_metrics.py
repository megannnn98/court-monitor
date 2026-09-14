from collections.abc import Sequence

from search.evaluation_models import ArticleReference


# на каком месте поисковой выдачи находится правильная статья
def reciprocal_rank(
    retrieved: Sequence[ArticleReference],
    expected_article: ArticleReference,
) -> float:
    for rank, article in enumerate(retrieved, start=1):
        if article == expected_article:
            return 1.0 / rank

    return 0.0


# считает среднее качество поиска по всем проверочным запросам
def mean_reciprocal_rank(reciprocal_ranks: Sequence[float]) -> float:
    if not reciprocal_ranks:
        return 0.0

    return sum(reciprocal_ranks) / len(reciprocal_ranks)
