import pytest

from hybrid_search import HybridSearch
from models import SearchHit, SearchQuery
from search_factory import (
    SearchBackendName,
    select_search_backend,
)


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
