from models import SearchHit, SearchQuery
from reranker import Reranker
from search_backend import SearchBackend


class RerankingSearch:
    def __init__(
        self,
        base_backend: SearchBackend,
        reranker: Reranker,
        *,
        candidate_multiplier: int = 3,
    ) -> None:
        if candidate_multiplier <= 0:
            raise ValueError("candidate_multiplier must be greater than 0")

        self._base_backend = base_backend
        self._reranker = reranker
        self._candidate_multiplier = candidate_multiplier

    def search(self, query: SearchQuery) -> list[SearchHit]:
        candidate_limit = min(
            query.limit * self._candidate_multiplier,
            100,
        )

        candidate_query = query.model_copy(
            update={"limit": candidate_limit},
        )

        candidates = self._base_backend.search(candidate_query)

        return self._reranker.rerank(
            query.text,
            candidates,
            limit=query.limit,
        )
