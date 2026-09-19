"""PgVectorStore specifics beyond the shared VectorStore contract (ADR 0018)."""

from __future__ import annotations

import math

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from semantic_retrieval.models import RetrievalEntityType, SemanticDocument
from semantic_retrieval.pgvector_store import (
    PgVectorStore,
    dense_search_sql,
    ef_search,
    prepare_dense_search,
)
from semantic_retrieval.vector_store import VectorPoint

MODEL = "test/model"
NAME = "pg_persons"


def _point(entity_id: int) -> VectorPoint:
    angle = entity_id / 400 * math.pi
    return VectorPoint(
        document=SemanticDocument(
            entity_type=RetrievalEntityType.PERSON,
            entity_id=entity_id,
            text="x",
            representation_version=1,
            content_hash="h",
        ),
        vector=[math.cos(angle), math.sin(angle), 0.1],
        embedding_model_id=MODEL,
    )


def test_ef_search_grows_with_the_limit_within_pgvector_bounds() -> None:
    assert [ef_search(limit) for limit in (1, 10, 100, 200, 500)] == [40, 40, 400, 800, 1000]


def test_a_search_returns_more_rows_than_pgvectors_default_ef_search(
    session_factory: sessionmaker[Session],
) -> None:
    """An HNSW scan stops at ef_search rows: at the default 40, a 150-candidate pool
    would come back with 40."""
    store = PgVectorStore(session_factory)
    store.ensure_collection(NAME, 3)
    store.upsert(NAME, [_point(entity_id) for entity_id in range(1, 201)])

    matches = store.search(NAME, [1.0, 0.0, 0.1], embedding_model_id=MODEL, limit=150)

    assert len(matches) == 150


def test_the_collection_gets_a_partial_hnsw_index_of_its_size(
    session_factory: sessionmaker[Session],
) -> None:
    store = PgVectorStore(session_factory)
    store.ensure_collection(NAME, 3)
    store.recreate_collection(NAME, 4)

    with session_factory() as session:
        definitions = (
            session.execute(
                text(
                    "SELECT indexdef FROM pg_indexes WHERE tablename = 'semantic_vectors' "
                    "AND indexname LIKE 'ix_semvec_hnsw_pg_persons_%'"
                )
            )
            .scalars()
            .all()
        )
    [definition] = definitions
    assert "ix_semvec_hnsw_pg_persons_4" in definition
    assert "(embedding)::vector(4)" in definition and "vector_cosine_ops" in definition
    assert "collection_name)::text = 'pg_persons'" in definition


def test_the_dense_search_uses_the_hnsw_index_even_with_stale_statistics(
    session_factory: sessionmaker[Session],
) -> None:
    """Statistics that describe an almost empty table (as before autoanalyze runs after a
    rebuild) make a sequential scan look cheaper; the search still takes the index."""
    store = PgVectorStore(session_factory)
    store.ensure_collection(NAME, 3)
    store.upsert(NAME, [_point(1)])
    with session_factory.begin() as session:
        session.execute(text("ANALYZE semantic_vectors"))
    store.upsert(NAME, [_point(entity_id) for entity_id in range(2, 401)])

    with session_factory.begin() as session:
        prepare_dense_search(session, 3, limit=10)
        plan = "\n".join(
            session.execute(
                text("EXPLAIN " + dense_search_sql(NAME, 3)),
                {"query": "[1,0,0.1]", "limit": 10},
            ).scalars()
        )

    assert "Index Scan using ix_semvec_hnsw_pg_persons_3" in plan, plan
