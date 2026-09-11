from collections.abc import Sequence

from models import SearchHit
from reranker import Reranker


class FakeReranker:
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


def test_fake_reranker_satisfies_reranker_contract() -> None:
    candidates = (
        make_hit(1),
        make_hit(2),
        make_hit(3),
    )
    expected = [
        candidates[1],
        candidates[0],
    ]

    fake = FakeReranker(expected)

    reranker: Reranker = fake

    result = reranker.rerank(
        "реабилитация нацизма",
        candidates,
        limit=2,
    )

    assert result == expected
    assert fake.query == "реабилитация нацизма"
    assert fake.candidates == list(candidates)
    assert fake.limit == 2
