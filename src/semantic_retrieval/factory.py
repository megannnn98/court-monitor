"""Composition of semantic retrieval from configuration (env).

Nothing here loads a model or connects to Qdrant: the embedder and reranker
load lazily on first use, and QdrantClient connects on the first request.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

from qdrant_client import QdrantClient
from sqlalchemy.orm import Session, sessionmaker

from semantic_retrieval.document_store import (
    PostgresLexicalEntityRetriever,
    SqlAlchemySemanticDocumentRepository,
)
from semantic_retrieval.documents import (
    EventSemanticDocumentBuilder,
    PersonSemanticDocumentBuilder,
)
from semantic_retrieval.embeddings import EmbeddingConfig, SentenceTransformerEmbedder, TextEmbedder
from semantic_retrieval.indexer import SemanticIndexer
from semantic_retrieval.models import (
    RetrievalBackend,
    RetrievalEntityType,
    SemanticConfigurationError,
)
from semantic_retrieval.relevance import (
    DEFAULT_DENSE_MIN_SCORE,
    DenseSimilarityRelevancePolicy,
    resolve_dense_min_score,
)
from semantic_retrieval.reranking import CrossEncoderReranker, Reranker, RerankerConfig
from semantic_retrieval.retrievers import (
    EntityRetriever,
    HybridEntityRetriever,
    QdrantEntityRetriever,
    RerankingEntityRetriever,
)
from semantic_retrieval.vector_store import QdrantVectorStore, VectorStore

DEFAULT_PERSON_COLLECTION = "persons_semantic"
DEFAULT_EVENT_COLLECTION = "events_semantic"
DEFAULT_CANDIDATE_POOL_SIZE = 100
MAX_CANDIDATE_POOL_SIZE = 200


def _flag(value: str | None) -> bool:
    return (value or "").strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class SemanticRetrievalConfig:
    # None: semantic retrieval is not configured (structured research still works).
    qdrant_url: str | None = None
    person_collection: str = DEFAULT_PERSON_COLLECTION
    event_collection: str = DEFAULT_EVENT_COLLECTION
    candidate_pool_size: int = DEFAULT_CANDIDATE_POOL_SIZE
    rerank: bool = False
    rerank_candidates: int = 50

    def __post_init__(self) -> None:
        if not 1 <= self.candidate_pool_size <= MAX_CANDIDATE_POOL_SIZE:
            raise SemanticConfigurationError(
                f"SEMANTIC_CANDIDATE_POOL_SIZE must be between 1 and {MAX_CANDIDATE_POOL_SIZE}"
            )

    @property
    def collections(self) -> dict[RetrievalEntityType, str]:
        return {
            RetrievalEntityType.PERSON: self.person_collection,
            RetrievalEntityType.EVENT: self.event_collection,
        }

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> SemanticRetrievalConfig:
        env = os.environ if env is None else env
        try:
            pool_size = int(env.get("SEMANTIC_CANDIDATE_POOL_SIZE") or DEFAULT_CANDIDATE_POOL_SIZE)
        except ValueError as exc:
            raise SemanticConfigurationError(
                "SEMANTIC_CANDIDATE_POOL_SIZE must be an integer"
            ) from exc
        return cls(
            qdrant_url=env.get("QDRANT_URL") or None,
            person_collection=env.get("PERSON_QDRANT_COLLECTION") or DEFAULT_PERSON_COLLECTION,
            event_collection=env.get("EVENT_QDRANT_COLLECTION") or DEFAULT_EVENT_COLLECTION,
            candidate_pool_size=pool_size,
            rerank=_flag(env.get("SEMANTIC_RERANK")),
        )


def create_vector_store(qdrant_url: str) -> QdrantVectorStore:
    """`:memory:` gives an in-process store (evaluation, experiments)."""
    if qdrant_url == ":memory:":
        return QdrantVectorStore(QdrantClient(":memory:"))
    # check_compatibility=False: no version request at construction, so wiring
    # the graph never touches the network; the first real call does.
    return QdrantVectorStore(QdrantClient(url=qdrant_url, timeout=10, check_compatibility=False))


@dataclass(frozen=True)
class SemanticComponents:
    session_factory: sessionmaker[Session]
    store: VectorStore
    embedder: TextEmbedder
    collections: Mapping[RetrievalEntityType, str]
    reranker: Reranker | None = None
    rerank_candidates: int = 50
    dense_min_score: float = DEFAULT_DENSE_MIN_SCORE

    def relevance_policy(self) -> DenseSimilarityRelevancePolicy:
        return DenseSimilarityRelevancePolicy(
            dense_min_score=self.dense_min_score, embedding_model_id=self.embedder.model_id
        )

    def retriever(self, backend: RetrievalBackend) -> EntityRetriever:
        lexical = PostgresLexicalEntityRetriever(self.session_factory)
        if backend is RetrievalBackend.LEXICAL:
            return lexical
        dense = QdrantEntityRetriever(
            embedder=self.embedder, store=self.store, collections=self.collections
        )
        if backend is RetrievalBackend.DENSE:
            return dense
        hybrid = HybridEntityRetriever(lexical=lexical, dense=dense)
        if backend is RetrievalBackend.HYBRID:
            return hybrid
        if backend is RetrievalBackend.HYBRID_RERANKED:
            if self.reranker is None:
                raise ValueError("hybrid_reranked needs a reranker")
            return RerankingEntityRetriever(
                base=hybrid,
                reranker=self.reranker,
                texts=SqlAlchemySemanticDocumentRepository(self.session_factory),
                candidate_limit=self.rerank_candidates,
            )
        raise ValueError(f"{backend.value} is not a retrieval backend")

    def indexer(self) -> SemanticIndexer:
        return SemanticIndexer(
            builders={
                RetrievalEntityType.PERSON: PersonSemanticDocumentBuilder(self.session_factory),
                RetrievalEntityType.EVENT: EventSemanticDocumentBuilder(self.session_factory),
            },
            repository=SqlAlchemySemanticDocumentRepository(self.session_factory),
            embedder=self.embedder,
            store=self.store,
            collections=self.collections,
        )


def create_semantic_components(
    session_factory: sessionmaker[Session],
    config: SemanticRetrievalConfig,
    env: Mapping[str, str] | None = None,
    *,
    with_reranker: bool | None = None,
) -> SemanticComponents:
    if config.qdrant_url is None:
        raise ValueError("QDRANT_URL is not set")
    use_reranker = config.rerank if with_reranker is None else with_reranker
    embedding_config = EmbeddingConfig.from_env(env)
    return SemanticComponents(
        session_factory=session_factory,
        store=create_vector_store(config.qdrant_url),
        embedder=SentenceTransformerEmbedder(embedding_config),
        collections=config.collections,
        reranker=CrossEncoderReranker(RerankerConfig.from_env(env)) if use_reranker else None,
        rerank_candidates=config.rerank_candidates,
        # Threshold is tied to the embedding model (fails for an uncalibrated model).
        dense_min_score=resolve_dense_min_score(
            os.environ if env is None else env, embedding_config.model_id
        ),
    )
