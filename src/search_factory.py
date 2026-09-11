from typing import Literal

from qdrant_client import QdrantClient
from sqlalchemy.orm import Session, sessionmaker

from hybrid_search import HybridSearch
from postgres_lexical_search import PostgresLexicalSearch
from qdrant_dense_search import QdrantDenseSearch
from search_backend import SearchBackend
from text_embedder import TextEmbedder

SearchBackendName = Literal[
    "lexical",
    "dense",
    "hybrid",
]


def create_lexical_search(
    session_factory: sessionmaker[Session],
) -> PostgresLexicalSearch:
    return PostgresLexicalSearch(session_factory)


def create_dense_search(
    *,
    client: QdrantClient,
    collection_name: str,
    embedder: TextEmbedder,
) -> QdrantDenseSearch:
    return QdrantDenseSearch(
        client=client,
        collection_name=collection_name,
        embedder=embedder,
    )


def create_hybrid_search(
    *,
    lexical_backend: SearchBackend,
    dense_backend: SearchBackend,
) -> HybridSearch:
    return HybridSearch(
        lexical_backend=lexical_backend,
        dense_backend=dense_backend,
    )


def select_search_backend(
    backend: SearchBackendName,
    *,
    lexical_backend: SearchBackend,
    dense_backend: SearchBackend | None = None,
) -> SearchBackend:
    if backend == "lexical":
        return lexical_backend

    if dense_backend is None:
        raise ValueError(f"dense backend is required for {backend} search")

    if backend == "dense":
        return dense_backend

    if backend == "hybrid":
        return create_hybrid_search(
            lexical_backend=lexical_backend,
            dense_backend=dense_backend,
        )

    raise ValueError(f"unknown search backend: {backend}")
