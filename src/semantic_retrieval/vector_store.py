"""Vector store port and its Qdrant implementation (candidate index only).

Payload is minimal — entity id/type, representation version, content hash.
No names, statuses or texts are copied to Qdrant: facts stay in PostgreSQL.
"""

from __future__ import annotations

import logging
import uuid
import warnings
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

import httpx
from qdrant_client import QdrantClient, models
from qdrant_client.http.exceptions import ResponseHandlingException, UnexpectedResponse

from semantic_retrieval.models import (
    IndexModelMismatchError,
    RetrievalEntityType,
    RetrievalUnavailableError,
    SemanticDocument,
    VectorSizeMismatchError,
)

logger = logging.getLogger("semantic_retrieval")

# Fixed namespace: point ids must never change between runs or machines.
POINT_ID_NAMESPACE = uuid.UUID("7d1b6a2e-3c1f-5b8e-9a4d-2f6c8e0b1a53")

_CLIENT_ERRORS = (ResponseHandlingException, UnexpectedResponse, httpx.HTTPError, OSError)


def _require_model(name: str, stored: object, expected: str) -> None:
    if stored != expected:
        # Points without the field predate model tracking: also incompatible.
        raise IndexModelMismatchError(
            f"Collection {name} was built with embedding model {stored or 'unknown'}, "
            f"not {expected}; run rebuild-semantic-index (full rebuild)"
        )


def point_id(entity_type: RetrievalEntityType, entity_id: int) -> str:
    """Deterministic Qdrant point id: re-indexing an entity overwrites its point."""
    return str(uuid.uuid5(POINT_ID_NAMESPACE, f"{entity_type.value}:{entity_id}"))


@dataclass(frozen=True)
class VectorPoint:
    document: SemanticDocument
    vector: list[float]
    # Model that produced `vector`; vectors of different models are not comparable.
    embedding_model_id: str


@dataclass(frozen=True)
class VectorMatch:
    entity_id: int
    score: float


class VectorStore(Protocol):
    def ensure_collection(self, name: str, vector_size: int) -> None: ...

    def recreate_collection(self, name: str, vector_size: int) -> None: ...

    def upsert(self, name: str, points: Sequence[VectorPoint]) -> None: ...

    def delete(
        self, name: str, entity_type: RetrievalEntityType, entity_ids: Sequence[int]
    ) -> None: ...

    def search(
        self,
        name: str,
        vector: Sequence[float],
        *,
        embedding_model_id: str,
        limit: int,
        entity_ids: Sequence[int] | None = None,
    ) -> list[VectorMatch]:
        """Raises IndexModelMismatchError if matched points come from another model."""
        ...

    def check_embedding_model(self, name: str, embedding_model_id: str) -> None:
        """Raise IndexModelMismatchError if the existing points use another model."""
        ...

    def count(self, name: str) -> int: ...


class QdrantVectorStore:
    def __init__(self, client: QdrantClient) -> None:
        self._client = client

    def _call[T](self, operation: str, action: Callable[[], T]) -> T:
        try:
            return action()
        except _CLIENT_ERRORS as exc:
            logger.warning(
                "vector_store_unavailable operation=%s error=%s", operation, type(exc).__name__
            )
            raise RetrievalUnavailableError(f"Qdrant unavailable during {operation}") from exc

    def _vector_size(self, name: str) -> int | None:
        if not self._call("collection_exists", lambda: self._client.collection_exists(name)):
            return None
        info = self._call("get_collection", lambda: self._client.get_collection(name))
        vectors = info.config.params.vectors
        if not isinstance(vectors, models.VectorParams):
            raise RetrievalUnavailableError(
                f"Collection {name} does not use a single unnamed vector"
            )
        return vectors.size

    def _create(self, name: str, vector_size: int) -> None:
        self._call(
            "create_collection",
            lambda: self._client.create_collection(
                collection_name=name,
                vectors_config=models.VectorParams(
                    size=vector_size, distance=models.Distance.COSINE
                ),
            ),
        )
        with warnings.catch_warnings():
            # Local (":memory:") mode ignores payload indexes and warns about it.
            warnings.filterwarnings("ignore", message="Payload indexes have no effect")
            self._call(
                "create_payload_index",
                lambda: self._client.create_payload_index(
                    collection_name=name,
                    field_name="entity_id",
                    field_schema=models.PayloadSchemaType.INTEGER,
                ),
            )

    def ensure_collection(self, name: str, vector_size: int) -> None:
        existing = self._vector_size(name)
        if existing is None:
            self._create(name, vector_size)
        elif existing != vector_size:
            raise VectorSizeMismatchError(
                f"Collection {name} has vector size {existing}, embedder produces {vector_size}; "
                "rebuild the semantic index"
            )

    def recreate_collection(self, name: str, vector_size: int) -> None:
        if self._vector_size(name) is not None:
            self._call("delete_collection", lambda: self._client.delete_collection(name))
        self._create(name, vector_size)

    def upsert(self, name: str, points: Sequence[VectorPoint]) -> None:
        if not points:
            return
        structs = [
            models.PointStruct(
                id=point_id(point.document.entity_type, point.document.entity_id),
                vector=point.vector,
                payload={
                    "entity_id": point.document.entity_id,
                    "entity_type": point.document.entity_type.value,
                    "representation_version": point.document.representation_version,
                    "content_hash": point.document.content_hash,
                    "embedding_model_id": point.embedding_model_id,
                },
            )
            for point in points
        ]
        self._call("upsert", lambda: self._client.upsert(collection_name=name, points=structs))

    def delete(
        self, name: str, entity_type: RetrievalEntityType, entity_ids: Sequence[int]
    ) -> None:
        if not entity_ids or self._vector_size(name) is None:
            return
        selector = models.PointIdsList(
            points=[point_id(entity_type, entity_id) for entity_id in entity_ids]
        )
        self._call(
            "delete", lambda: self._client.delete(collection_name=name, points_selector=selector)
        )

    def search(
        self,
        name: str,
        vector: Sequence[float],
        *,
        embedding_model_id: str,
        limit: int,
        entity_ids: Sequence[int] | None = None,
    ) -> list[VectorMatch]:
        size = self._vector_size(name)
        if size is None:
            raise RetrievalUnavailableError(
                f"Collection {name} does not exist; run rebuild-semantic-index"
            )
        if size != len(vector):
            raise VectorSizeMismatchError(
                f"Query vector size {len(vector)} differs from collection {name} size {size}"
            )
        query_filter = (
            models.Filter(
                must=[
                    models.FieldCondition(
                        key="entity_id", match=models.MatchAny(any=list(entity_ids))
                    )
                ]
            )
            if entity_ids is not None
            else None
        )
        response = self._call(
            "query_points",
            lambda: self._client.query_points(
                collection_name=name,
                query=list(vector),
                query_filter=query_filter,
                limit=limit,
                with_payload=["entity_id", "embedding_model_id"],
            ),
        )
        matches: list[VectorMatch] = []
        for point in response.points:
            payload = point.payload or {}
            _require_model(name, payload.get("embedding_model_id"), embedding_model_id)
            entity_id = payload.get("entity_id")
            if isinstance(entity_id, int):
                matches.append(VectorMatch(entity_id=entity_id, score=float(point.score)))
        return matches

    def check_embedding_model(self, name: str, embedding_model_id: str) -> None:
        if self._vector_size(name) is None:
            return
        # Any point of another model, not a sample: points without the field match too.
        foreign = models.Filter(
            must_not=[
                models.FieldCondition(
                    key="embedding_model_id", match=models.MatchValue(value=embedding_model_id)
                )
            ]
        )
        records, _ = self._call(
            "scroll",
            lambda: self._client.scroll(
                collection_name=name,
                scroll_filter=foreign,
                limit=1,
                with_payload=["embedding_model_id"],
            ),
        )
        for record in records:
            _require_model(
                name, (record.payload or {}).get("embedding_model_id"), embedding_model_id
            )

    def count(self, name: str) -> int:
        if self._vector_size(name) is None:
            return 0
        return self._call(
            "count", lambda: self._client.count(collection_name=name, exact=True).count
        )
