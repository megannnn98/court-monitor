"""SemanticIndexer: stable points, incremental re-embedding, stale deletion."""

from __future__ import annotations

import pytest
from qdrant_client import QdrantClient
from support.semantic_fakes import HashingEmbedder, InMemoryBuilder, InMemoryDocumentRepository

from semantic_retrieval.indexer import SemanticIndexer
from semantic_retrieval.models import (
    IndexModelMismatchError,
    RetrievalEntityType,
    RetrievalNotConfiguredError,
)
from semantic_retrieval.vector_store import QdrantVectorStore

PERSON = RetrievalEntityType.PERSON
EVENT = RetrievalEntityType.EVENT
COLLECTIONS = {PERSON: "persons_semantic", EVENT: "events_semantic"}


def _indexer(
    texts: dict[int, str],
) -> tuple[
    SemanticIndexer, InMemoryBuilder, HashingEmbedder, QdrantVectorStore, InMemoryDocumentRepository
]:
    builder = InMemoryBuilder(PERSON, dict(texts))
    embedder = HashingEmbedder()
    store = QdrantVectorStore(QdrantClient(":memory:"))
    repository = InMemoryDocumentRepository()
    indexer = SemanticIndexer(
        builders={PERSON: builder},
        repository=repository,
        embedder=embedder,
        store=store,
        collections=COLLECTIONS,
    )
    return indexer, builder, embedder, store, repository


def test_rebuild_indexes_all_entities_in_batches() -> None:
    indexer, _, embedder, store, repository = _indexer({1: "a", 2: "b", 3: "c"})

    stats = indexer.rebuild(PERSON, batch_size=2)

    assert (stats.documents_built, stats.embedded, stats.unchanged, stats.deleted) == (3, 3, 0, 0)
    assert [len(call) for call in embedder.document_calls] == [2, 1]
    assert store.count("persons_semantic") == 3
    assert repository.list_entity_ids(PERSON) == [1, 2, 3]


def test_incremental_run_skips_unchanged_and_reembeds_changed() -> None:
    indexer, builder, embedder, store, _ = _indexer({1: "a", 2: "b"})
    indexer.rebuild(PERSON)
    embedder.document_calls.clear()

    builder.texts[2] = "b changed"
    stats = indexer.rebuild(PERSON, incremental=True)

    assert (stats.embedded, stats.unchanged) == (1, 1)
    assert embedder.document_calls == [["b changed"]]
    assert store.count("persons_semantic") == 2  # same point overwritten, not duplicated


def test_full_rebuild_reembeds_even_unchanged_documents() -> None:
    indexer, _, embedder, _, _ = _indexer({1: "a"})
    indexer.rebuild(PERSON)

    stats = indexer.rebuild(PERSON)

    assert (stats.embedded, stats.unchanged) == (1, 0)
    assert len(embedder.document_calls) == 2


def test_rebuild_deletes_stale_entities_only_after_a_full_scan() -> None:
    indexer, builder, _, store, repository = _indexer({1: "a", 2: "b", 3: "c"})
    indexer.rebuild(PERSON)
    del builder.texts[3]

    limited = indexer.rebuild(PERSON, incremental=True, limit=1)
    assert limited.deleted == 0
    full = indexer.rebuild(PERSON, incremental=True)

    assert full.deleted == 1
    assert repository.list_entity_ids(PERSON) == [1, 2]
    assert store.count("persons_semantic") == 2


def test_index_entities_updates_given_ids_and_removes_vanished_ones() -> None:
    indexer, builder, embedder, store, repository = _indexer({1: "a", 2: "b"})
    indexer.rebuild(PERSON)
    embedder.document_calls.clear()
    builder.texts[1] = "a2"
    del builder.texts[2]

    stats = indexer.index_entities(PERSON, [1, 2])

    assert (stats.embedded, stats.deleted) == (1, 1)
    assert repository.list_entity_ids(PERSON) == [1]
    assert store.count("persons_semantic") == 1


def test_delete_entities_removes_points_and_documents() -> None:
    indexer, _, _, store, repository = _indexer({1: "a", 2: "b"})
    indexer.rebuild(PERSON)

    indexer.delete_entities(PERSON, [2])

    assert store.count("persons_semantic") == 1
    assert repository.list_entity_ids(PERSON) == [1]


def test_failed_embedding_leaves_document_pending_for_the_next_run() -> None:
    indexer, builder, embedder, _, repository = _indexer({1: "a"})
    indexer.rebuild(PERSON)
    builder.texts[1] = "changed"

    def broken(texts: object) -> list[list[float]]:
        raise RuntimeError("CUDA out of memory")

    embedder.embed_documents = broken  # type: ignore[method-assign]
    with pytest.raises(RuntimeError):
        indexer.rebuild(PERSON, incremental=True)

    assert repository.get_states(PERSON, [1])[1].indexed is False


def test_unconfigured_entity_type_is_an_error() -> None:
    indexer, *_ = _indexer({})

    with pytest.raises(RetrievalNotConfiguredError):
        indexer.rebuild(EVENT)


def test_incremental_indexing_refuses_a_collection_of_another_model() -> None:
    indexer, _, embedder, _, _ = _indexer({1: "a"})
    indexer.rebuild(PERSON)
    embedder.model_id = "another-model"

    with pytest.raises(IndexModelMismatchError):
        indexer.rebuild(PERSON, incremental=True)
    with pytest.raises(IndexModelMismatchError):
        indexer.index_entities(PERSON, [1])
    assert indexer.rebuild(PERSON).embedded == 1  # a full rebuild replaces the index
