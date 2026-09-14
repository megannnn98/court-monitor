"""Dense, hybrid and reranked entity retrieval with fakes."""

from __future__ import annotations

import logging

import pytest
from qdrant_client import QdrantClient
from semantic_fakes import (
    HashingEmbedder,
    InMemoryDocumentRepository,
    KeywordReranker,
    StaticRetriever,
    UnavailableStore,
    document,
)

from semantic_retrieval.models import (
    RetrievalBackend,
    RetrievalEntityType,
    RetrievalFilters,
    RetrievalNotConfiguredError,
    RetrievalQuery,
    RetrievalUnavailableError,
)
from semantic_retrieval.reranking import RetrievalCandidate, order_by_scores
from semantic_retrieval.retrievers import (
    HybridEntityRetriever,
    QdrantEntityRetriever,
    RerankingEntityRetriever,
)
from semantic_retrieval.vector_store import QdrantVectorStore, VectorPoint

PERSON = RetrievalEntityType.PERSON
LEXICAL = RetrievalBackend.LEXICAL
DENSE = RetrievalBackend.DENSE


def _query(
    text: str = "пикет против войны", limit: int = 10, entity_ids: list[int] | None = None
) -> RetrievalQuery:
    return RetrievalQuery(
        text=text, entity_type=PERSON, limit=limit, filters=RetrievalFilters(entity_ids=entity_ids)
    )


def _dense(texts: dict[int, str]) -> tuple[QdrantEntityRetriever, HashingEmbedder]:
    embedder = HashingEmbedder()
    store = QdrantVectorStore(QdrantClient(":memory:"))
    store.ensure_collection("persons_semantic", embedder.dimension)
    docs = [document(entity_id, text) for entity_id, text in texts.items()]
    store.upsert(
        "persons_semantic",
        [
            VectorPoint(doc, vector)
            for doc, vector in zip(
                docs, embedder.embed_documents([d.text for d in docs]), strict=True
            )
        ],
    )
    retriever = QdrantEntityRetriever(
        embedder=embedder, store=store, collections={PERSON: "persons_semantic"}
    )
    return retriever, embedder


# --- dense ---------------------------------------------------------------------------


def test_dense_embeds_query_once_and_maps_points_to_ranked_entity_hits() -> None:
    retriever, embedder = _dense(
        {1: "одиночный пикет против войны", 2: "штраф за парковку", 3: "пикет у суда"}
    )

    result = retriever.retrieve(_query(limit=2))

    assert embedder.query_calls == ["пикет против войны"]
    assert result.backend is DENSE
    assert result.entity_ids == [1, 3]
    assert [hit.rank for hit in result.hits] == [1, 2]
    assert result.hits[0].score >= result.hits[1].score
    assert all(hit.entity_type is PERSON and hit.backend is DENSE for hit in result.hits)


def test_dense_respects_entity_id_filter() -> None:
    retriever, _ = _dense({1: "пикет против войны", 2: "пикет"})

    assert retriever.retrieve(_query(entity_ids=[2])).entity_ids == [2]


def test_dense_unavailable_store_raises_not_empty_result() -> None:
    retriever = QdrantEntityRetriever(
        embedder=HashingEmbedder(), store=UnavailableStore(), collections={PERSON: "p"}
    )

    with pytest.raises(RetrievalUnavailableError):
        retriever.retrieve(_query())


def test_dense_without_collection_for_entity_type_is_not_configured() -> None:
    retriever, _ = _dense({1: "a"})

    with pytest.raises(RetrievalNotConfiguredError):
        retriever.retrieve(RetrievalQuery(text="x", entity_type=RetrievalEntityType.EVENT))


# --- hybrid --------------------------------------------------------------------------


def test_hybrid_keeps_lexical_only_dense_only_and_shared_hits_once() -> None:
    lexical = StaticRetriever(LEXICAL, [1, 2])
    dense = StaticRetriever(DENSE, [3, 1])
    hybrid = HybridEntityRetriever(lexical=lexical, dense=dense, per_backend_limit=50)

    result = hybrid.retrieve(_query(limit=10))

    assert result.backend is RetrievalBackend.HYBRID
    assert result.entity_ids == [1, 3, 2]  # 1 found by both ranks first
    assert len(set(result.entity_ids)) == len(result.entity_ids)
    assert result.hits[0].component_ranks == {"lexical": 1, "dense": 2}
    # Each backend is asked for the larger candidate pool, not just the final limit.
    assert [q.limit for q in lexical.queries] == [50]
    assert [q.limit for q in dense.queries] == [50]


def test_hybrid_is_deterministic_on_ties() -> None:
    hybrid = HybridEntityRetriever(
        lexical=StaticRetriever(LEXICAL, [5]), dense=StaticRetriever(DENSE, [4])
    )

    orders = {tuple(hybrid.retrieve(_query()).entity_ids) for _ in range(5)}

    assert orders == {(5, 4)}


def test_hybrid_propagates_dense_failure_instead_of_returning_lexical_only() -> None:
    hybrid = HybridEntityRetriever(
        lexical=StaticRetriever(LEXICAL, [1]),
        dense=StaticRetriever(DENSE, error=RetrievalUnavailableError("down")),
    )

    with pytest.raises(RetrievalUnavailableError):
        hybrid.retrieve(_query())


def test_hybrid_logs_counts_without_query_text(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="semantic_retrieval")
    hybrid = HybridEntityRetriever(
        lexical=StaticRetriever(LEXICAL, [1, 2]), dense=StaticRetriever(DENSE, [2])
    )

    hybrid.retrieve(_query(text="секретная формулировка"))

    messages = [record.getMessage() for record in caplog.records]
    assert any("retrieval_started backend=hybrid" in m for m in messages)
    assert any(
        "retrieval_finished backend=hybrid lexical_count=2 dense_count=1 fused_count=2" in m
        for m in messages
    )
    assert not any("секретная" in m for m in messages)


# --- reranking -----------------------------------------------------------------------


def test_reranker_reorders_candidates_by_document_text_and_applies_top_k() -> None:
    repository = InMemoryDocumentRepository()
    repository.upsert(
        [document(1, "штраф за парковку"), document(2, "пикет против войны"), document(3, "пикет")]
    )
    reranker = KeywordReranker()
    retriever = RerankingEntityRetriever(
        base=StaticRetriever(RetrievalBackend.HYBRID, [1, 3, 2]),
        reranker=reranker,
        texts=repository,
        candidate_limit=20,
    )

    result = retriever.retrieve(_query(text="пикет против войны", limit=2))

    assert reranker.calls == [[1, 3, 2]]  # input order preserved into the reranker
    assert result.backend is RetrievalBackend.HYBRID_RERANKED
    assert result.entity_ids == [2, 3]
    assert [hit.rank for hit in result.hits] == [1, 2]
    assert result.hits[0].component_ranks == {"hybrid": 3}


def test_reranker_skips_candidates_without_stored_document() -> None:
    repository = InMemoryDocumentRepository()
    repository.upsert([document(1, "пикет")])
    retriever = RerankingEntityRetriever(
        base=StaticRetriever(RetrievalBackend.HYBRID, [9, 1]),
        reranker=KeywordReranker(),
        texts=repository,
    )

    assert retriever.retrieve(_query(text="пикет")).entity_ids == [1]


def test_equal_reranker_scores_keep_input_order() -> None:
    base = StaticRetriever(RetrievalBackend.HYBRID, [3, 1, 2]).retrieve(_query())
    candidates = [RetrievalCandidate(hit=hit, text="x") for hit in base.hits]

    hits = order_by_scores(candidates, [0.5, 0.5, 0.9], limit=3)

    assert [hit.entity_id for hit in hits] == [2, 3, 1]


def test_reranker_is_not_called_when_no_candidate_has_a_stored_document() -> None:
    reranker = KeywordReranker()
    retriever = RerankingEntityRetriever(
        base=StaticRetriever(RetrievalBackend.HYBRID, [7, 8]),
        reranker=reranker,
        texts=InMemoryDocumentRepository(),
    )

    result = retriever.retrieve(_query(text="пикет"))

    assert result.hits == []
    assert result.backend is RetrievalBackend.HYBRID_RERANKED
    assert reranker.calls == []
