"""SemanticIndexer on each vector store with the PostgreSQL document repository:
incremental indexing behaves the same, and it never continues another backend's index."""

from __future__ import annotations

import pytest
from qdrant_client import QdrantClient
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker
from support.semantic_fakes import HashingEmbedder, InMemoryBuilder

from semantic_retrieval.document_store import SqlAlchemySemanticDocumentRepository
from semantic_retrieval.indexer import SemanticIndexer
from semantic_retrieval.models import IndexBackendMismatchError, RetrievalEntityType
from semantic_retrieval.pgvector_store import PgVectorStore
from semantic_retrieval.vector_store import QdrantVectorStore, VectorStore

PERSON = RetrievalEntityType.PERSON
COLLECTION = "idx_persons"


def _store(backend: str, session_factory: sessionmaker[Session]) -> VectorStore:
    if backend == "pgvector":
        return PgVectorStore(session_factory)
    return QdrantVectorStore(QdrantClient(":memory:"))


def _indexer(
    store: VectorStore, builder: InMemoryBuilder, session_factory: sessionmaker[Session]
) -> tuple[SemanticIndexer, HashingEmbedder]:
    embedder = HashingEmbedder()
    indexer = SemanticIndexer(
        builders={PERSON: builder},
        repository=SqlAlchemySemanticDocumentRepository(session_factory),
        embedder=embedder,
        store=store,
        collections={PERSON: COLLECTION},
    )
    return indexer, embedder


def _top(store: VectorStore, embedder: HashingEmbedder, text: str) -> int:
    [best, *_] = sorted(
        store.search(
            COLLECTION, embedder.embed_query(text), embedding_model_id=embedder.model_id, limit=5
        ),
        key=lambda match: -match.score,
    )
    return best.entity_id


@pytest.mark.parametrize("backend", ["qdrant", "pgvector"])
def test_incremental_indexing_inserts_updates_deletes_and_skips_unchanged(
    backend: str, session_factory: sessionmaker[Session]
) -> None:
    store = _store(backend, session_factory)
    builder = InMemoryBuilder(PERSON, {1: "суд в Москве", 2: "обыск у журналиста", 3: "штраф"})
    indexer, embedder = _indexer(store, builder, session_factory)
    indexer.rebuild(PERSON)
    embedder.document_calls.clear()

    builder.texts[2] = "приговор активисту"  # update
    builder.texts[4] = "арест блогера"  # insert
    del builder.texts[3]  # delete
    stats = indexer.rebuild(PERSON, incremental=True)

    assert (stats.embedded, stats.unchanged, stats.deleted) == (2, 1, 1)
    assert embedder.document_calls == [["приговор активисту", "арест блогера"]]
    assert store.count(COLLECTION) == 3
    assert _top(store, embedder, "приговор активисту") == 2
    assert _top(store, embedder, "арест блогера") == 4

    again = indexer.rebuild(PERSON, incremental=True)
    assert (again.embedded, again.unchanged, again.deleted) == (0, 3, 0)


@pytest.mark.parametrize("backend", ["qdrant", "pgvector"])
def test_index_entities_reembeds_changed_and_removes_vanished(
    backend: str, session_factory: sessionmaker[Session]
) -> None:
    store = _store(backend, session_factory)
    builder = InMemoryBuilder(PERSON, {1: "суд", 2: "обыск"})
    indexer, _ = _indexer(store, builder, session_factory)
    indexer.rebuild(PERSON)

    builder.texts[1] = "суд изменён"
    del builder.texts[2]
    stats = indexer.index_entities(PERSON, [1, 2])

    assert (stats.embedded, stats.unchanged, stats.deleted) == (1, 0, 1)
    assert store.count(COLLECTION) == 1


def test_switching_backend_needs_a_full_rebuild_first(
    session_factory: sessionmaker[Session],
) -> None:
    """Qdrant's marks would make pgvector skip every document: an error, not an empty
    index. After a full pgvector rebuild, pgvector continues and Qdrant refuses."""
    builder = InMemoryBuilder(PERSON, {1: "суд", 2: "обыск"})
    qdrant, _ = _indexer(QdrantVectorStore(QdrantClient(":memory:")), builder, session_factory)
    pgvector_store = PgVectorStore(session_factory)
    pgvector, _ = _indexer(pgvector_store, builder, session_factory)
    qdrant.rebuild(PERSON)

    with pytest.raises(IndexBackendMismatchError, match="built in qdrant, not pgvector"):
        pgvector.rebuild(PERSON, incremental=True)
    with pytest.raises(IndexBackendMismatchError):
        pgvector.index_entities(PERSON, [1])
    assert pgvector_store.count(COLLECTION) == 0

    full = pgvector.rebuild(PERSON)
    assert (full.embedded, pgvector_store.count(COLLECTION)) == (2, 2)
    assert pgvector.rebuild(PERSON, incremental=True).unchanged == 2
    with pytest.raises(IndexBackendMismatchError, match="built in pgvector, not qdrant"):
        qdrant.rebuild(PERSON, incremental=True)


def test_a_first_incremental_run_claims_an_unrecorded_index(
    session_factory: sessionmaker[Session],
) -> None:
    builder = InMemoryBuilder(PERSON, {1: "суд", 2: "обыск"})
    store = PgVectorStore(session_factory)
    indexer, _ = _indexer(store, builder, session_factory)

    stats = indexer.rebuild(PERSON, incremental=True)

    assert (stats.embedded, store.count(COLLECTION)) == (2, 2)
    assert SqlAlchemySemanticDocumentRepository(session_factory).get_index_backend(PERSON) == (
        "pgvector"
    )


def test_marks_without_a_recorded_backend_need_a_full_rebuild(
    session_factory: sessionmaker[Session],
) -> None:
    """A restore without semantic_index_state leaves indexed_at marks nobody owns: an
    incremental run would skip those documents in an index that never got them."""
    builder = InMemoryBuilder(PERSON, {1: "суд", 2: "обыск"})
    store = PgVectorStore(session_factory)
    indexer, _ = _indexer(store, builder, session_factory)
    indexer.rebuild(PERSON)
    with session_factory.begin() as session:
        session.execute(text("DELETE FROM semantic_index_state"))
        session.execute(text("DELETE FROM semantic_vectors"))

    with pytest.raises(IndexBackendMismatchError, match="no recorded backend"):
        indexer.rebuild(PERSON, incremental=True)
    with pytest.raises(IndexBackendMismatchError):
        indexer.index_entities(PERSON, [1])

    assert indexer.rebuild(PERSON).embedded == 2
    assert indexer.rebuild(PERSON, incremental=True).unchanged == 2
