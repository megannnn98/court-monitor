"""In-memory fakes for semantic retrieval tests (no model downloads, no Qdrant service)."""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import NoReturn

from semantic_retrieval.document_store import StoredDocumentState
from semantic_retrieval.documents import compute_content_hash
from semantic_retrieval.models import (
    RetrievalBackend,
    RetrievalEntityType,
    RetrievalHit,
    RetrievalQuery,
    RetrievalResult,
    RetrievalUnavailableError,
    SemanticDocument,
)
from semantic_retrieval.reranking import RetrievalCandidate, order_by_scores
from semantic_retrieval.vector_store import VectorMatch, VectorPoint

_WORD = re.compile(r"\w+")


@dataclass
class HashingEmbedder:
    """Bag-of-words vectors via hashing: similar word sets give similar vectors."""

    dimension: int = 64
    model_id: str = "fake-hashing-embedder"
    query_calls: list[str] = field(default_factory=list)
    document_calls: list[list[str]] = field(default_factory=list)

    def _vector(self, text: str) -> list[float]:
        vector = [0.0] * self.dimension
        for word in _WORD.findall(text.lower()):
            bucket = int(hashlib.md5(word.encode()).hexdigest(), 16) % self.dimension
            vector[bucket] += 1.0
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [value / norm for value in vector]

    def embed_query(self, text: str) -> list[float]:
        self.query_calls.append(text)
        return self._vector(text)

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        self.document_calls.append(list(texts))
        return [self._vector(text) for text in texts]


def document(
    entity_id: int, text: str, *, entity_type: RetrievalEntityType = RetrievalEntityType.PERSON
) -> SemanticDocument:
    return SemanticDocument(
        entity_type=entity_type,
        entity_id=entity_id,
        text=text,
        representation_version=1,
        content_hash=compute_content_hash(entity_type, 1, text),
    )


@dataclass
class InMemoryBuilder:
    entity_type: RetrievalEntityType
    texts: dict[int, str] = field(default_factory=dict)

    def list_entity_ids(self, *, limit: int | None = None) -> list[int]:
        return sorted(self.texts)[:limit]

    def build(self, entity_ids: Sequence[int]) -> list[SemanticDocument]:
        return [
            document(entity_id, self.texts[entity_id], entity_type=self.entity_type)
            for entity_id in sorted(set(entity_ids))
            if entity_id in self.texts
        ]


@dataclass
class InMemoryDocumentRepository:
    rows: dict[tuple[RetrievalEntityType, int], tuple[SemanticDocument, bool]] = field(
        default_factory=dict
    )

    def get_states(
        self, entity_type: RetrievalEntityType, entity_ids: Sequence[int]
    ) -> dict[int, StoredDocumentState]:
        return {
            entity_id: StoredDocumentState(doc.content_hash, doc.representation_version, indexed)
            for (row_type, entity_id), (doc, indexed) in self.rows.items()
            if row_type is entity_type and entity_id in entity_ids
        }

    def upsert(self, documents: Sequence[SemanticDocument]) -> None:
        for doc in documents:
            key = (doc.entity_type, doc.entity_id)
            previous = self.rows.get(key)
            unchanged = previous is not None and (
                previous[0].content_hash,
                previous[0].representation_version,
            ) == (doc.content_hash, doc.representation_version)
            self.rows[key] = (doc, bool(unchanged and previous and previous[1]))

    def mark_indexed(
        self, entity_type: RetrievalEntityType, entity_ids: Sequence[int], indexed_at: datetime
    ) -> None:
        for entity_id in entity_ids:
            doc, _ = self.rows[(entity_type, entity_id)]
            self.rows[(entity_type, entity_id)] = (doc, True)

    def clear_indexed(self, entity_type: RetrievalEntityType) -> None:
        for key, (doc, _) in list(self.rows.items()):
            if key[0] is entity_type:
                self.rows[key] = (doc, False)

    def delete(self, entity_type: RetrievalEntityType, entity_ids: Sequence[int]) -> None:
        for entity_id in entity_ids:
            self.rows.pop((entity_type, entity_id), None)

    def list_entity_ids(self, entity_type: RetrievalEntityType) -> list[int]:
        return sorted(entity_id for row_type, entity_id in self.rows if row_type is entity_type)

    def get_texts(self, entity_type: RetrievalEntityType, entity_ids: list[int]) -> dict[int, str]:
        return {
            entity_id: self.rows[(entity_type, entity_id)][0].text
            for entity_id in entity_ids
            if (entity_type, entity_id) in self.rows
        }


@dataclass
class StaticRetriever:
    """Returns fixed entity ids in order; records queries."""

    backend: RetrievalBackend
    entity_ids: list[int] = field(default_factory=list)
    error: Exception | None = None
    queries: list[RetrievalQuery] = field(default_factory=list)
    # Dense cosine similarity per entity id, stored as the dense component score.
    dense_scores: dict[int, float] = field(default_factory=dict)

    def retrieve(self, query: RetrievalQuery) -> RetrievalResult:
        self.queries.append(query)
        if self.error is not None:
            raise self.error
        ids = [
            entity_id
            for entity_id in self.entity_ids
            if query.filters.entity_ids is None or entity_id in query.filters.entity_ids
        ][: query.limit]
        return RetrievalResult(
            entity_type=query.entity_type,
            backend=self.backend,
            hits=[
                RetrievalHit(
                    entity_type=query.entity_type,
                    entity_id=entity_id,
                    score=1.0 / rank,
                    backend=self.backend,
                    rank=rank,
                    component_scores=(
                        {"dense": self.dense_scores[entity_id]}
                        if entity_id in self.dense_scores
                        else {}
                    ),
                )
                for rank, entity_id in enumerate(ids, start=1)
            ],
        )


@dataclass
class KeywordReranker:
    """Scores a candidate by how many query words its text contains."""

    calls: list[list[int]] = field(default_factory=list)

    def rerank(
        self, query: str, candidates: Sequence[RetrievalCandidate], *, limit: int
    ) -> list[RetrievalHit]:
        self.calls.append([candidate.hit.entity_id for candidate in candidates])
        words = set(_WORD.findall(query.lower()))
        scores = [
            float(len(words & set(_WORD.findall(candidate.text.lower()))))
            for candidate in candidates
        ]
        return order_by_scores(candidates, scores, limit=limit)


class UnavailableStore:
    """A vector store whose every call fails like an unreachable Qdrant."""

    def _fail(self, operation: str) -> NoReturn:
        raise RetrievalUnavailableError(f"Qdrant unavailable during {operation}")

    def ensure_collection(self, name: str, vector_size: int) -> None:
        self._fail("ensure_collection")

    def recreate_collection(self, name: str, vector_size: int) -> None:
        self._fail("recreate_collection")

    def upsert(self, name: str, points: Sequence[VectorPoint]) -> None:
        self._fail("upsert")

    def delete(
        self, name: str, entity_type: RetrievalEntityType, entity_ids: Sequence[int]
    ) -> None:
        self._fail("delete")

    def search(
        self,
        name: str,
        vector: Sequence[float],
        *,
        embedding_model_id: str,
        limit: int,
        entity_ids: Sequence[int] | None = None,
    ) -> list[VectorMatch]:
        self._fail("search")

    def check_embedding_model(self, name: str, embedding_model_id: str) -> None:
        self._fail("check_embedding_model")

    def count(self, name: str) -> int:
        self._fail("count")
