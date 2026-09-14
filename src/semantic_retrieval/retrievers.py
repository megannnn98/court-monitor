"""Entity retrievers: dense (Qdrant), hybrid (lexical + dense + RRF), reranked.

Every retriever returns candidate entity ids with backend, rank and score.
None of them loads or returns facts about the entities.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Protocol

from semantic_retrieval.embeddings import TextEmbedder
from semantic_retrieval.models import (
    RetrievalBackend,
    RetrievalEntityType,
    RetrievalHit,
    RetrievalNotConfiguredError,
    RetrievalQuery,
    RetrievalResult,
)
from semantic_retrieval.reranking import Reranker, RetrievalCandidate
from semantic_retrieval.rrf import DEFAULT_RRF_K, reciprocal_rank_fusion
from semantic_retrieval.vector_store import VectorStore

logger = logging.getLogger("semantic_retrieval")


class EntityRetriever(Protocol):
    def retrieve(self, query: RetrievalQuery) -> RetrievalResult: ...


class DocumentTextSource(Protocol):
    def get_texts(
        self, entity_type: RetrievalEntityType, entity_ids: list[int]
    ) -> dict[int, str]: ...


class QdrantEntityRetriever:
    """text → query embedding → Qdrant nearest neighbours → RetrievalHit[]."""

    def __init__(
        self,
        *,
        embedder: TextEmbedder,
        store: VectorStore,
        collections: Mapping[RetrievalEntityType, str],
    ) -> None:
        self._embedder = embedder
        self._store = store
        self._collections = collections

    def retrieve(self, query: RetrievalQuery) -> RetrievalResult:
        collection = self._collections.get(query.entity_type)
        if collection is None:
            raise RetrievalNotConfiguredError(
                f"No vector collection configured for {query.entity_type.value}"
            )
        vector = self._embedder.embed_query(query.text)
        matches = self._store.search(
            collection, vector, limit=query.limit, entity_ids=query.filters.entity_ids
        )
        ordered = sorted(matches, key=lambda match: (-match.score, match.entity_id))
        hits = [
            RetrievalHit(
                entity_type=query.entity_type,
                entity_id=match.entity_id,
                score=match.score,
                backend=RetrievalBackend.DENSE,
                rank=rank,
            )
            for rank, match in enumerate(ordered, start=1)
        ]
        logger.info("dense_retrieval entity_type=%s count=%d", query.entity_type.value, len(hits))
        return RetrievalResult(
            entity_type=query.entity_type, backend=RetrievalBackend.DENSE, hits=hits
        )


class HybridEntityRetriever:
    """Lexical and dense candidates fused with RRF, deduplicated by (type, id)."""

    def __init__(
        self,
        *,
        lexical: EntityRetriever,
        dense: EntityRetriever,
        per_backend_limit: int = 100,
        rrf_k: int = DEFAULT_RRF_K,
    ) -> None:
        self._lexical = lexical
        self._dense = dense
        self._per_backend_limit = per_backend_limit
        self._rrf_k = rrf_k

    def retrieve(self, query: RetrievalQuery) -> RetrievalResult:
        logger.info(
            "retrieval_started backend=hybrid entity_type=%s query_chars=%d limit=%d",
            query.entity_type.value,
            len(query.text),
            query.limit,
        )
        component_query = query.model_copy(
            update={"limit": max(query.limit, self._per_backend_limit)}
        )
        lexical = self._lexical.retrieve(component_query)
        dense = self._dense.retrieve(component_query)
        fused = reciprocal_rank_fusion(
            {RetrievalBackend.LEXICAL: lexical.hits, RetrievalBackend.DENSE: dense.hits},
            limit=query.limit,
            rrf_k=self._rrf_k,
        )
        logger.info(
            "retrieval_finished backend=hybrid lexical_count=%d dense_count=%d fused_count=%d",
            len(lexical.hits),
            len(dense.hits),
            len(fused),
        )
        return RetrievalResult(
            entity_type=query.entity_type, backend=RetrievalBackend.HYBRID, hits=fused
        )


class RerankingEntityRetriever:
    """Rerank the top candidates of another retriever with a cross-encoder."""

    def __init__(
        self,
        *,
        base: EntityRetriever,
        reranker: Reranker,
        texts: DocumentTextSource,
        candidate_limit: int = 50,
    ) -> None:
        self._base = base
        self._reranker = reranker
        self._texts = texts
        self._candidate_limit = candidate_limit

    def retrieve(self, query: RetrievalQuery) -> RetrievalResult:
        base = self._base.retrieve(
            query.model_copy(update={"limit": max(query.limit, self._candidate_limit)})
        )
        texts = self._texts.get_texts(query.entity_type, base.entity_ids)
        # A hit without a stored document (deleted meanwhile) cannot be reranked.
        candidates = [
            RetrievalCandidate(hit=hit, text=texts[hit.entity_id])
            for hit in base.hits
            if hit.entity_id in texts
        ]
        hits: list[RetrievalHit] = (
            self._reranker.rerank(query.text, candidates, limit=query.limit) if candidates else []
        )
        logger.info(
            "retrieval_reranked candidates=%d reranked_count=%d", len(candidates), len(hits)
        )
        return RetrievalResult(
            entity_type=query.entity_type, backend=RetrievalBackend.HYBRID_RERANKED, hits=hits
        )
