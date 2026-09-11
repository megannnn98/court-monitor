from collections.abc import Sequence

import pytest

from models import SearchHit, SearchQuery
from reranking_search import RerankingSearch
from search_backend import SearchBackend


class StubSearchBackend:
    def __init__(self, hits: Sequence[SearchHit]) -> None:
        self._hits = list(hits)
        self.queries: list[SearchQuery] = []

    def search(self, query: SearchQuery) -> list[SearchHit]:
        self.queries.append(query)
        return list(self._hits)


class StubReranker:
    def __init__(self, result: Sequence[SearchHit]) -> None:
        self._result = list(result)
        self.query: str | None = None
        self.candidates: list[SearchHit] = []
        self.limit: int | None = None

    def rerank(
        self,
        query: str,
        candidates: Sequence[SearchHit],
        *,
        limit: int,
    ) -> list[SearchHit]:
        self.query = query
        self.candidates = list(candidates)
        self.limit = limit

        return list(self._result)


def make_hit(chunk_id: int) -> SearchHit:
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
        score=1.0,
    )


def test_reranking_search_requests_extended_candidate_pool() -> None:
    candidates = [
        make_hit(1),
        make_hit(2),
        make_hit(3),
    ]
    reranked = [
        candidates[2],
        candidates[0],
    ]

    base_backend = StubSearchBackend(candidates)
    reranker = StubReranker(reranked)

    search: SearchBackend = RerankingSearch(
        base_backend=base_backend,
        reranker=reranker,
        candidate_multiplier=3,
    )

    result = search.search(
        SearchQuery(
            text="реабилитация нацизма",
            limit=2,
        )
    )

    assert result == reranked

    assert base_backend.queries == [
        SearchQuery(
            text="реабилитация нацизма",
            limit=6,
        )
    ]

    assert reranker.query == "реабилитация нацизма"
    assert reranker.candidates == candidates
    assert reranker.limit == 2


def test_candidate_limit_is_capped_at_100() -> None:
    base_backend = StubSearchBackend([])
    reranker = StubReranker([])

    search = RerankingSearch(
        base_backend=base_backend,
        reranker=reranker,
        candidate_multiplier=3,
    )

    search.search(
        SearchQuery(
            text="реабилитация нацизма",
            limit=50,
        )
    )

    assert base_backend.queries == [
        SearchQuery(
            text="реабилитация нацизма",
            limit=100,
        )
    ]


@pytest.mark.parametrize(
    "candidate_multiplier",
    [
        0,
        -1,
    ],
)
def test_non_positive_candidate_multiplier_is_rejected(
    candidate_multiplier: int,
) -> None:
    base_backend = StubSearchBackend([])
    reranker = StubReranker([])

    with pytest.raises(
        ValueError,
        match="candidate_multiplier must be greater than 0",
    ):
        RerankingSearch(
            base_backend=base_backend,
            reranker=reranker,
            candidate_multiplier=candidate_multiplier,
        )
