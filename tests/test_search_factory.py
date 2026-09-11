from collections.abc import Sequence

import pytest

from hybrid_search import HybridSearch
from models import SearchHit, SearchQuery
from reranking_search import RerankingSearch
from search_factory import (
    SearchBackendName,
    select_search_backend,
)


class StubReranker:
    def rerank(
        self,
        query: str,
        candidates: Sequence[SearchHit],
        *,
        limit: int,
    ) -> list[SearchHit]:
        return list(candidates)[:limit]


class StubSearchBackend:
    def search(self, query: SearchQuery) -> list[SearchHit]:
        return []


def test_selects_lexical_backend() -> None:
    lexical_backend = StubSearchBackend()

    result = select_search_backend(
        "lexical",
        lexical_backend=lexical_backend,
    )

    assert result is lexical_backend


def test_selects_dense_backend() -> None:
    lexical_backend = StubSearchBackend()
    dense_backend = StubSearchBackend()

    result = select_search_backend(
        "dense",
        lexical_backend=lexical_backend,
        dense_backend=dense_backend,
    )

    assert result is dense_backend


def test_creates_hybrid_backend() -> None:
    lexical_backend = StubSearchBackend()
    dense_backend = StubSearchBackend()

    result = select_search_backend(
        "hybrid",
        lexical_backend=lexical_backend,
        dense_backend=dense_backend,
    )

    assert isinstance(result, HybridSearch)


@pytest.mark.parametrize(
    "backend",
    [
        "dense",
        "hybrid",
        "reranked-hybrid",
    ],
)
def test_dense_backend_is_required(
    backend: SearchBackendName,
) -> None:
    lexical_backend = StubSearchBackend()

    with pytest.raises(
        ValueError,
        match="dense backend is required",
    ):
        select_search_backend(
            backend,
            lexical_backend=lexical_backend,
        )


def test_creates_reranked_hybrid_backend() -> None:
    lexical_backend = StubSearchBackend()
    dense_backend = StubSearchBackend()
    reranker = StubReranker()

    result = select_search_backend(
        "reranked-hybrid",
        lexical_backend=lexical_backend,
        dense_backend=dense_backend,
        reranker=reranker,
    )

    assert isinstance(result, RerankingSearch)


def test_reranker_is_required_for_reranked_hybrid() -> None:
    lexical_backend = StubSearchBackend()
    dense_backend = StubSearchBackend()

    with pytest.raises(
        ValueError,
        match="reranker is required for reranked-hybrid search",
    ):
        select_search_backend(
            "reranked-hybrid",
            lexical_backend=lexical_backend,
            dense_backend=dense_backend,
        )
