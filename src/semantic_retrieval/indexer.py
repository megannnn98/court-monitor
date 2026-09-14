"""Build semantic documents, store them in PostgreSQL and upsert vectors.

Incremental by default for single entities: a document whose content hash
and representation version are unchanged and already indexed is not
re-embedded. A full rebuild recreates the collection and re-embeds everything.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from semantic_retrieval.document_store import StoredDocumentState
from semantic_retrieval.documents import SemanticDocumentBuilder
from semantic_retrieval.embeddings import TextEmbedder
from semantic_retrieval.models import (
    RetrievalEntityType,
    RetrievalNotConfiguredError,
    SemanticDocument,
)
from semantic_retrieval.vector_store import VectorPoint, VectorStore

logger = logging.getLogger("semantic_retrieval")

DEFAULT_BATCH_SIZE = 64


class SemanticDocumentRepository(Protocol):
    def get_states(
        self, entity_type: RetrievalEntityType, entity_ids: Sequence[int]
    ) -> dict[int, StoredDocumentState]: ...

    def upsert(self, documents: Sequence[SemanticDocument]) -> None: ...

    def mark_indexed(
        self, entity_type: RetrievalEntityType, entity_ids: Sequence[int], indexed_at: datetime
    ) -> None: ...

    def clear_indexed(self, entity_type: RetrievalEntityType) -> None: ...

    def delete(self, entity_type: RetrievalEntityType, entity_ids: Sequence[int]) -> None: ...

    def list_entity_ids(self, entity_type: RetrievalEntityType) -> list[int]: ...


@dataclass
class IndexingStats:
    entity_type: RetrievalEntityType
    documents_built: int = 0
    embedded: int = 0
    unchanged: int = 0
    deleted: int = 0


class SemanticIndexer:
    def __init__(
        self,
        *,
        builders: Mapping[RetrievalEntityType, SemanticDocumentBuilder],
        repository: SemanticDocumentRepository,
        embedder: TextEmbedder,
        store: VectorStore,
        collections: Mapping[RetrievalEntityType, str],
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._builders = builders
        self._repository = repository
        self._embedder = embedder
        self._store = store
        self._collections = collections
        self._clock = clock

    def _builder(self, entity_type: RetrievalEntityType) -> SemanticDocumentBuilder:
        builder = self._builders.get(entity_type)
        if builder is None or entity_type not in self._collections:
            raise RetrievalNotConfiguredError(
                f"Semantic indexing not configured for {entity_type.value}"
            )
        return builder

    def rebuild(
        self,
        entity_type: RetrievalEntityType,
        *,
        batch_size: int = DEFAULT_BATCH_SIZE,
        limit: int | None = None,
        incremental: bool = False,
    ) -> IndexingStats:
        """Index every indexable entity (or the first `limit`).

        Full rebuild recreates the collection. Stale documents (entity gone or
        inactive) are deleted only when all entities were scanned (no limit).
        """
        if batch_size <= 0:
            raise ValueError("batch_size must be greater than 0")
        builder = self._builder(entity_type)
        collection = self._collections[entity_type]
        if incremental:
            self._store.ensure_collection(collection, self._embedder.dimension)
        else:
            self._store.recreate_collection(collection, self._embedder.dimension)
            self._repository.clear_indexed(entity_type)

        stats = IndexingStats(entity_type=entity_type)
        entity_ids = builder.list_entity_ids(limit=limit)
        logger.info(
            "semantic_index_started entity_type=%s entities=%d incremental=%s",
            entity_type.value,
            len(entity_ids),
            incremental,
        )
        for start in range(0, len(entity_ids), batch_size):
            self._index_documents(builder.build(entity_ids[start : start + batch_size]), stats)

        if limit is None:
            stale = sorted(set(self._repository.list_entity_ids(entity_type)) - set(entity_ids))
            self._delete(entity_type, stale, stats)
        logger.info(
            "semantic_index_finished entity_type=%s built=%d embedded=%d unchanged=%d deleted=%d",
            entity_type.value,
            stats.documents_built,
            stats.embedded,
            stats.unchanged,
            stats.deleted,
        )
        return stats

    def index_entities(
        self, entity_type: RetrievalEntityType, entity_ids: Sequence[int]
    ) -> IndexingStats:
        """Incrementally (re)index the given entities; vanished ones are deleted."""
        builder = self._builder(entity_type)
        self._store.ensure_collection(self._collections[entity_type], self._embedder.dimension)
        stats = IndexingStats(entity_type=entity_type)
        documents = builder.build(entity_ids)
        self._index_documents(documents, stats)
        built = {document.entity_id for document in documents}
        self._delete(entity_type, sorted(set(entity_ids) - built), stats)
        return stats

    def delete_entities(
        self, entity_type: RetrievalEntityType, entity_ids: Sequence[int]
    ) -> IndexingStats:
        self._builder(entity_type)
        stats = IndexingStats(entity_type=entity_type)
        self._delete(entity_type, list(entity_ids), stats)
        return stats

    def _index_documents(self, documents: Sequence[SemanticDocument], stats: IndexingStats) -> None:
        if not documents:
            return
        entity_type = documents[0].entity_type
        stats.documents_built += len(documents)
        # Upsert first: a changed document loses its indexed mark, so a failed
        # embedding/upsert below is retried by the next incremental run.
        self._repository.upsert(documents)
        states = self._repository.get_states(entity_type, [d.entity_id for d in documents])
        pending = [
            document
            for document in documents
            if not (state := states.get(document.entity_id)) or not state.indexed
        ]
        stats.unchanged += len(documents) - len(pending)
        if not pending:
            return
        vectors = self._embedder.embed_documents([document.text for document in pending])
        self._store.upsert(
            self._collections[entity_type],
            [VectorPoint(document=d, vector=v) for d, v in zip(pending, vectors, strict=True)],
        )
        self._repository.mark_indexed(entity_type, [d.entity_id for d in pending], self._clock())
        stats.embedded += len(pending)

    def _delete(
        self, entity_type: RetrievalEntityType, entity_ids: Sequence[int], stats: IndexingStats
    ) -> None:
        if not entity_ids:
            return
        self._store.delete(self._collections[entity_type], entity_type, entity_ids)
        self._repository.delete(entity_type, entity_ids)
        stats.deleted += len(entity_ids)
