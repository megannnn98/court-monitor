from collections.abc import Sequence
from typing import Protocol

from models import SearchHit


class Reranker(Protocol):
    def rerank(
        self,
        query: str,
        candidates: Sequence[SearchHit],
        *,
        limit: int,
    ) -> list[SearchHit]:
        """
        Rerank candidates for the query.

        Returned hits contain reranker scores in SearchHit.score.

        The result contains at most `limit` hits. A non-positive
        limit is invalid. Empty candidates produce an empty result.

        Candidates with equal reranker scores preserve their input order.
        """
        ...
