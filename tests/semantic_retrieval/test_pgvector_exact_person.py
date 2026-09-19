"""PERSON in pgvector: exact search, no HNSW index, vectors stored PLAIN (ADR 0018).

EVENT keeps its HNSW index; tests/semantic_retrieval/test_pgvector_store.py covers it.
"""

from __future__ import annotations

import math
import random

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker
from support.semantic_fakes import HashingEmbedder, InMemoryBuilder

from semantic_retrieval.document_store import SqlAlchemySemanticDocumentRepository
from semantic_retrieval.factory import SemanticRetrievalConfig, create_configured_vector_store
from semantic_retrieval.indexer import SemanticIndexer
from semantic_retrieval.models import (
    IndexBackendMismatchError,
    RetrievalEntityType,
    SemanticDocument,
)
from semantic_retrieval.pgvector_store import PgVectorStore
from semantic_retrieval.vector_store import VectorPoint

MODEL = "test/model"
PERSONS = "persons_semantic"
EVENTS = "events_semantic"
DIMENSION = 16


def _point(entity_id: int, vector: list[float]) -> VectorPoint:
    return VectorPoint(
        document=SemanticDocument(
            entity_type=RetrievalEntityType.PERSON,
            entity_id=entity_id,
            text="x",
            representation_version=1,
            content_hash="h",
        ),
        vector=vector,
        embedding_model_id=MODEL,
    )


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    return dot / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))


def _store(session_factory: sessionmaker[Session]) -> PgVectorStore:
    """As the factory builds it: the person collection exact, the event one HNSW."""
    store = create_configured_vector_store(
        SemanticRetrievalConfig(vector_backend="pgvector"), session_factory
    )
    assert isinstance(store, PgVectorStore)
    return store


def _hnsw_indexes(session_factory: sessionmaker[Session]) -> list[str]:
    """HNSW indexes of these tests' collections (other tests' indexes outlive TRUNCATE)."""
    with session_factory() as session:
        return sorted(
            session.execute(
                text(
                    "SELECT indexname FROM pg_indexes WHERE tablename = 'semantic_vectors' "
                    "AND (indexname LIKE :persons OR indexname LIKE :events)"
                ),
                {"persons": f"ix_semvec_hnsw_{PERSONS}_%", "events": f"ix_semvec_hnsw_{EVENTS}_%"},
            ).scalars()
        )


def test_person_search_is_exactly_the_brute_force_ranking(
    session_factory: sessionmaker[Session],
) -> None:
    rng = random.Random(7)
    vectors = {i: [rng.gauss(0, 1) for _ in range(DIMENSION)] for i in range(1, 501)}
    store = _store(session_factory)
    store.ensure_collection(PERSONS, DIMENSION)
    store.upsert(PERSONS, [_point(i, v) for i, v in vectors.items()])

    for _ in range(10):
        query = [rng.gauss(0, 1) for _ in range(DIMENSION)]
        reference = sorted(vectors, key=lambda i: (-_cosine(vectors[i], query), i))[:100]

        matches = store.search(PERSONS, query, embedding_model_id=MODEL, limit=100)

        assert [m.entity_id for m in matches] == reference
        for match in matches:
            assert match.score == pytest.approx(_cosine(vectors[match.entity_id], query), abs=1e-5)


def test_no_hnsw_index_is_created_for_persons_but_events_keep_theirs(
    session_factory: sessionmaker[Session],
) -> None:
    store = _store(session_factory)
    store.recreate_collection(EVENTS, DIMENSION)  # drops what an earlier test left
    store.recreate_collection(PERSONS, DIMENSION)
    store.ensure_collection(PERSONS, DIMENSION)

    assert _hnsw_indexes(session_factory) == [f"ix_semvec_hnsw_{EVENTS}_{DIMENSION}"]


def test_the_person_query_plan_has_no_approximate_path(
    session_factory: sessionmaker[Session],
) -> None:
    store = _store(session_factory)
    store.ensure_collection(PERSONS, DIMENSION)
    store.upsert(PERSONS, [_point(i, [float(i % 5 + 1)] * DIMENSION) for i in range(1, 51)])

    with session_factory() as session:
        plan = "\n".join(
            session.execute(
                text("EXPLAIN " + store.exact_search_sql(PERSONS)),
                {"query": "[" + ",".join(["1"] * DIMENSION) + "]", "limit": 10},
            ).scalars()
        )

    assert "hnsw" not in plan.lower()
    assert "vector_dims" not in plan
    assert "Sort" in plan


def test_person_vectors_are_stored_in_the_row_not_in_toast(
    session_factory: sessionmaker[Session],
) -> None:
    """768 float32 values are 3 KB, above the TOAST threshold: PLAIN keeps them inline,
    so an exact scan reads the heap instead of detoasting every row."""
    store = _store(session_factory)
    store.ensure_collection(PERSONS, 768)
    store.upsert(PERSONS, [_point(i, [0.001 * (i + j) for j in range(768)]) for i in range(1, 21)])

    with session_factory() as session:
        storage = session.execute(
            text(
                "SELECT attstorage FROM pg_attribute WHERE attrelid = "
                "'semantic_vectors'::regclass AND attname = 'embedding'"
            )
        ).scalar_one()
        toast = session.execute(
            text(
                "SELECT reltoastrelid::regclass::text FROM pg_class "
                "WHERE oid = 'semantic_vectors'::regclass"
            )
        ).scalar_one()
        toasted = session.execute(text(f"SELECT count(*) FROM {toast}")).scalar_one()

    assert storage == "p"
    assert toasted == 0


def test_switching_to_pgvector_needs_the_full_rebuild_that_writes_persons_plain(
    session_factory: sessionmaker[Session],
) -> None:
    """Marks left by a Qdrant rebuild stop an incremental pgvector run; the full rebuild
    then writes every person vector anew, exact and PLAIN."""
    repository = SqlAlchemySemanticDocumentRepository(session_factory)
    builder = InMemoryBuilder(RetrievalEntityType.PERSON, {1: "суд", 2: "обыск", 3: "арест"})
    indexer = SemanticIndexer(
        builders={RetrievalEntityType.PERSON: builder},
        repository=repository,
        embedder=HashingEmbedder(),
        store=_store(session_factory),
        collections={RetrievalEntityType.PERSON: PERSONS},
    )
    repository.set_index_backend(RetrievalEntityType.PERSON, "qdrant")

    with pytest.raises(IndexBackendMismatchError):
        indexer.rebuild(RetrievalEntityType.PERSON, incremental=True)

    stats = indexer.rebuild(RetrievalEntityType.PERSON)

    assert stats.embedded == 3
    assert repository.get_index_backend(RetrievalEntityType.PERSON) == "pgvector"
    assert not [i for i in _hnsw_indexes(session_factory) if PERSONS in i]
    assert indexer.rebuild(RetrievalEntityType.PERSON, incremental=True).unchanged == 3


def test_a_candidate_set_search_scores_like_the_brute_force(
    session_factory: sessionmaker[Session],
) -> None:
    """The entity_ids path (a structured candidate set) is exact too, one distance a row."""
    rng = random.Random(11)
    vectors = {i: [rng.gauss(0, 1) for _ in range(DIMENSION)] for i in range(1, 201)}
    store = _store(session_factory)
    store.ensure_collection(EVENTS, DIMENSION)
    store.upsert(EVENTS, [_point(i, v) for i, v in vectors.items()])
    candidates = sorted(rng.sample(sorted(vectors), 60))
    query = [rng.gauss(0, 1) for _ in range(DIMENSION)]

    matches = store.search(EVENTS, query, embedding_model_id=MODEL, limit=20, entity_ids=candidates)

    reference = sorted(candidates, key=lambda i: (-_cosine(vectors[i], query), i))[:20]
    assert [m.entity_id for m in matches] == reference
    for match in matches:
        assert match.score == pytest.approx(_cosine(vectors[match.entity_id], query), abs=1e-5)
    with session_factory() as session:
        plan = list(
            session.execute(
                text("EXPLAIN (VERBOSE) " + store.candidate_search_sql(EVENTS)),
                {
                    "query": "[" + ",".join(["1"] * DIMENSION) + "]",
                    "collection": EVENTS,
                    "ids": candidates,
                    "limit": 20,
                },
            ).scalars()
        )
    # The scan node computes the row's output: one distance, not a score and a distance.
    scan_output = [line for line in plan if "Output:" in line][-1]
    assert scan_output.count("<=>") == 1, plan


def test_ensure_drops_an_hnsw_index_left_from_before_persons_were_exact(
    session_factory: sessionmaker[Session],
) -> None:
    PgVectorStore(session_factory).recreate_collection(PERSONS, DIMENSION)  # HNSW, as before
    assert f"ix_semvec_hnsw_{PERSONS}_{DIMENSION}" in _hnsw_indexes(session_factory)

    _store(session_factory).ensure_collection(PERSONS, DIMENSION)

    assert not [i for i in _hnsw_indexes(session_factory) if PERSONS in i]
