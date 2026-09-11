from models import SearchHit, SearchQuery
from rrf import reciprocal_rank_fusion
from search_backend import SearchBackend


class HybridSearch:
    def __init__(
        self,
        lexical_backend: SearchBackend,
        dense_backend: SearchBackend,
        *,
        rrf_k: int = 60,
    ) -> None:
        self._lexical_backend = lexical_backend
        self._dense_backend = dense_backend
        self._rrf_k = rrf_k

    def search(self, query: SearchQuery) -> list[SearchHit]:
        candidate_limit = min(query.limit * 3, 100)

        candidate_query = query.model_copy(update={"limit": candidate_limit})

        lexical_hits = self._lexical_backend.search(candidate_query)
        dense_hits = self._dense_backend.search(candidate_query)

        return reciprocal_rank_fusion(
            lexical_hits,
            dense_hits,
            limit=query.limit,
            rrf_k=self._rrf_k,
        )
