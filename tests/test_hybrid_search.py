from hybrid_search import HybridSearch
from models import SearchHit, SearchQuery


class StubSearchBackend:
    def __init__(self, hits: list[SearchHit]) -> None:
        self._hits = hits
        self.queries: list[SearchQuery] = []

    def search(self, query: SearchQuery) -> list[SearchHit]:
        self.queries.append(query)
        return list(self._hits)


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


def test_hybrid_search_fuses_backend_results() -> None:
    lexical_backend = StubSearchBackend(
        [
            make_hit(1),
            make_hit(2),
            make_hit(3),
        ]
    )
    dense_backend = StubSearchBackend(
        [
            make_hit(3),
            make_hit(1),
            make_hit(4),
        ]
    )

    search = HybridSearch(
        lexical_backend=lexical_backend,
        dense_backend=dense_backend,
        rrf_k=10,
    )

    result = search.search(
        SearchQuery(
            text="реабилитация нацизма",
            limit=2,
        )
    )

    assert [hit.chunk_id for hit in result] == [1, 3]

    assert lexical_backend.queries == [
        SearchQuery(
            text="реабилитация нацизма",
            limit=6,
        )
    ]
    assert dense_backend.queries == [
        SearchQuery(
            text="реабилитация нацизма",
            limit=6,
        )
    ]
