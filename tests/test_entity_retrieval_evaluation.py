"""Entity retrieval evaluation corpus, honest labels and per-backend metrics (PostgreSQL)."""

from __future__ import annotations

from pathlib import Path

import pytest
from qdrant_client import QdrantClient
from semantic_fakes import HashingEmbedder
from sqlalchemy.orm import Session, sessionmaker

from semantic_retrieval.document_store import PostgresLexicalEntityRetriever
from semantic_retrieval.evaluation import (
    EntityRetrievalCase,
    evaluate_backend,
    format_comparison,
    load_cases,
    load_corpus,
    seed_corpus,
)
from semantic_retrieval.factory import SemanticComponents
from semantic_retrieval.models import RetrievalBackend, RetrievalEntityType, RetrievalQuery
from semantic_retrieval.vector_store import QdrantVectorStore

FIXTURES = Path(__file__).parent / "fixtures"
CORPUS = load_corpus(FIXTURES / "entity_retrieval_corpus.json")
CASES = load_cases(FIXTURES / "entity_retrieval_cases.json")
COLLECTIONS = {
    RetrievalEntityType.PERSON: "eval_persons_semantic",
    RetrievalEntityType.EVENT: "eval_events_semantic",
}


def test_corpus_and_cases_are_large_enough_and_consistent() -> None:
    person_keys = {person.key for person in CORPUS.persons}
    event_keys = {event.key for person in CORPUS.persons for event in person.events}

    assert len(person_keys) >= 10
    ranking_cases = [case for case in CASES if not case.negative]
    assert 6 <= len(ranking_cases) <= 20
    assert sum(case.semantic_only for case in CASES) >= 3
    negatives = [case for case in CASES if case.negative]
    assert len(negatives) >= 3
    assert all(case.judgments == {} for case in negatives)
    # A negative query sharing a word with the corpus ("суд"): lexical overlap
    # alone must not make anything relevant.
    assert any("суд" in case.query_text for case in negatives)
    assert {case.entity_type for case in CASES} == {
        RetrievalEntityType.PERSON,
        RetrievalEntityType.EVENT,
    }
    for case in CASES:
        keys = person_keys if case.entity_type is RetrievalEntityType.PERSON else event_keys
        assert set(case.judgments) <= keys, case.query_id
        assert case.negative or sum(grade > 0 for grade in case.judgments.values()) >= 1


def test_case_without_relevant_entity_is_invalid() -> None:
    with pytest.raises(ValueError, match="no relevant entity"):
        EntityRetrievalCase(
            query_id="q", query_text="x", entity_type=RetrievalEntityType.PERSON, judgments={"a": 0}
        )


def _components(session_factory: sessionmaker[Session]) -> SemanticComponents:
    return SemanticComponents(
        session_factory=session_factory,
        store=QdrantVectorStore(QdrantClient(":memory:")),
        embedder=HashingEmbedder(),
        collections=COLLECTIONS,
    )


def test_semantic_only_labels_are_honest_lexical_finds_no_relevant_entity(
    session_factory: sessionmaker[Session],
) -> None:
    """A case marked semantic_only must have no stemmed word overlap with any
    relevant document; otherwise the label would flatter dense retrieval."""
    ids = seed_corpus(session_factory, CORPUS)
    components = _components(session_factory)
    for entity_type in RetrievalEntityType:
        components.indexer().rebuild(entity_type)
    lexical = PostgresLexicalEntityRetriever(session_factory)

    for case in CASES:
        if not case.semantic_only or case.negative:
            continue
        relevant = {
            ids.ids_for(case.entity_type)[key] for key, grade in case.judgments.items() if grade > 0
        }
        found = lexical.retrieve(
            RetrievalQuery(text=case.query_text, entity_type=case.entity_type, limit=1000)
        ).entity_ids
        assert relevant.isdisjoint(found), case.query_id


def test_backends_are_evaluated_separately_with_bounded_metrics(
    session_factory: sessionmaker[Session],
) -> None:
    ids = seed_corpus(session_factory, CORPUS)
    components = _components(session_factory)
    for entity_type in RetrievalEntityType:
        components.indexer().rebuild(entity_type)

    evaluations = [
        evaluate_backend(
            backend=backend, retriever=components.retriever(backend), cases=CASES, ids=ids, k=5
        )
        for backend in (RetrievalBackend.LEXICAL, RetrievalBackend.DENSE, RetrievalBackend.HYBRID)
    ]

    for evaluation in evaluations:
        assert evaluation.overall.cases == sum(not case.negative for case in CASES)
        assert evaluation.semantic_only.cases == sum(case.semantic_only for case in CASES)
        for value in (
            evaluation.overall.mrr,
            evaluation.overall.recall_at_k,
            evaluation.overall.ndcg_at_k,
            evaluation.overall.precision_at_k,
        ):
            assert 0.0 <= value <= 1.0
    lexical = evaluations[0]
    assert lexical.semantic_only.mrr == 0.0  # by construction of semantic_only
    assert lexical.overall.mrr > 0.0
    table = format_comparison(evaluations)
    assert table.splitlines()[0].startswith("| backend | MRR | Recall@5 | nDCG@5")
    assert [line.split("|")[1].strip() for line in table.splitlines()[2:]] == [
        "lexical",
        "dense",
        "hybrid",
    ]
