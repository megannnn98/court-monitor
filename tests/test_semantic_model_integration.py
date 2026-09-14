"""Real multilingual embedder and cross-encoder (opt-in, downloads/loads models).

uv sync --group semantic
SEMANTIC_MODEL_TESTS=1 uv run pytest -m semantic_models
"""

from __future__ import annotations

import os

import pytest

from semantic_retrieval.embeddings import EmbeddingConfig, SentenceTransformerEmbedder
from semantic_retrieval.models import RetrievalBackend, RetrievalEntityType, RetrievalHit
from semantic_retrieval.reranking import CrossEncoderReranker, RerankerConfig, RetrievalCandidate

pytestmark = [
    pytest.mark.semantic_models,
    pytest.mark.skipif(
        os.environ.get("SEMANTIC_MODEL_TESTS") != "1", reason="SEMANTIC_MODEL_TESTS=1 not set"
    ),
]

ANTI_WAR = "Персона: Иван Иванов.\nСобытия:\n- арест, 2022-03-10: выступил против вторжения России в Украину."
UNRELATED = "Персона: Пётр Петров.\nСобытия:\n- штраф, 2023-07-01: оштрафован за превышение скорости на трассе."
RELIGION = "Персона: Павел Козлов.\nСобытия:\n- приговор: участие в организации «Свидетели Иеговы»."


def _cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


def test_e5_embeds_query_and_passages_with_semantics_beyond_word_overlap() -> None:
    embedder = SentenceTransformerEmbedder(EmbeddingConfig.from_env())

    query = embedder.embed_query("антивоенная позиция")
    anti_war, unrelated, religion = embedder.embed_documents([ANTI_WAR, UNRELATED, RELIGION])

    assert embedder.dimension == 768
    assert len(query) == 768
    assert _cosine(query, anti_war) > _cosine(query, unrelated)
    assert _cosine(query, anti_war) > _cosine(query, religion)


def test_cross_encoder_ranks_the_semantically_relevant_document_first() -> None:
    reranker = CrossEncoderReranker(RerankerConfig.from_env())
    candidates = [
        RetrievalCandidate(
            hit=RetrievalHit(
                entity_type=RetrievalEntityType.PERSON,
                entity_id=entity_id,
                score=0.0,
                backend=RetrievalBackend.HYBRID,
                rank=entity_id,
            ),
            text=text,
        )
        for entity_id, text in [(1, UNRELATED), (2, ANTI_WAR), (3, RELIGION)]
    ]

    reranked = reranker.rerank("преследование за антивоенные высказывания", candidates, limit=3)

    assert reranked[0].entity_id == 2


def test_default_threshold_rejects_off_topic_queries_and_accepts_a_relevant_one() -> None:
    from qdrant_client import QdrantClient

    from semantic_retrieval.models import RetrievalQuery, SemanticDocument
    from semantic_retrieval.relevance import DEFAULT_DENSE_MIN_SCORE, DenseSimilarityRelevancePolicy
    from semantic_retrieval.retrievers import QdrantEntityRetriever
    from semantic_retrieval.vector_store import QdrantVectorStore, VectorPoint

    embedder = SentenceTransformerEmbedder(EmbeddingConfig.from_env())
    store = QdrantVectorStore(QdrantClient(":memory:"))
    store.ensure_collection("persons", embedder.dimension)
    texts = {1: ANTI_WAR, 2: UNRELATED, 3: RELIGION}
    vectors = embedder.embed_documents(list(texts.values()))
    store.upsert(
        "persons",
        [
            VectorPoint(
                SemanticDocument(
                    entity_type=RetrievalEntityType.PERSON,
                    entity_id=entity_id,
                    text=text,
                    representation_version=1,
                    content_hash=str(entity_id),
                ),
                vector,
                embedder.model_id,
            )
            for (entity_id, text), vector in zip(texts.items(), vectors, strict=True)
        ],
    )
    dense = QdrantEntityRetriever(
        embedder=embedder, store=store, collections={RetrievalEntityType.PERSON: "persons"}
    )
    policy = DenseSimilarityRelevancePolicy(dense_min_score=DEFAULT_DENSE_MIN_SCORE)

    for off_topic in ("выращивание бананов на Марсе", "рецепт борща"):
        query = RetrievalQuery(text=off_topic, entity_type=RetrievalEntityType.PERSON)
        decision = policy.accept(query, dense.retrieve(query))
        assert decision.retrieved.hits  # nearest neighbours always exist
        assert decision.accepted.hits == [], off_topic

    query = RetrievalQuery(
        text="выступал против вторжения в Украину", entity_type=RetrievalEntityType.PERSON
    )
    assert policy.accept(query, dense.retrieve(query)).accepted.entity_ids[:1] == [1]
