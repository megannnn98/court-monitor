"""One VectorStore contract, run against every implementation (ADR 0018).

Qdrant runs in qdrant-client local mode (always); pgvector needs TEST_DATABASE_URL on a
PostgreSQL with the `vector` extension (skipped otherwise).
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterator

import pytest
from qdrant_client import QdrantClient
from sqlalchemy.orm import Session, sessionmaker

from db.database import create_database_engine, create_session_factory
from semantic_retrieval.models import (
    IndexModelMismatchError,
    RetrievalEntityType,
    RetrievalUnavailableError,
    SemanticDocument,
    VectorSizeMismatchError,
)
from semantic_retrieval.pgvector_store import PgVectorStore
from semantic_retrieval.vector_store import QdrantVectorStore, VectorPoint, VectorStore

MODEL = "test/model-a"
OTHER_MODEL = "test/model-b"
NAME = "contract_persons"
PERSON = RetrievalEntityType.PERSON
EVENT = RetrievalEntityType.EVENT


def _qdrant(request: pytest.FixtureRequest) -> VectorStore:
    return QdrantVectorStore(QdrantClient(":memory:"))


def _pgvector(request: pytest.FixtureRequest) -> VectorStore:
    session_factory: sessionmaker[Session] = request.getfixturevalue("session_factory")
    return PgVectorStore(session_factory)


def _pgvector_exact(request: pytest.FixtureRequest) -> VectorStore:
    """The person collection's mode: exact search, no HNSW index."""
    session_factory: sessionmaker[Session] = request.getfixturevalue("session_factory")
    return PgVectorStore(session_factory, exact_collections={NAME})


STORES: dict[str, Callable[[pytest.FixtureRequest], VectorStore]] = {
    "qdrant": _qdrant,
    "pgvector": _pgvector,
    "pgvector-exact": _pgvector_exact,
}


@pytest.fixture(params=sorted(STORES))
def store(request: pytest.FixtureRequest) -> VectorStore:
    return STORES[request.param](request)


def _unreachable_qdrant() -> VectorStore:
    return QdrantVectorStore(
        QdrantClient(url="http://127.0.0.1:9", timeout=1, check_compatibility=False)
    )


def _unreachable_pgvector() -> VectorStore:
    engine = create_database_engine(
        "postgresql+psycopg://nobody:nothing@127.0.0.1:9/court_monitor_test",
    )
    return PgVectorStore(create_session_factory(engine))


@pytest.fixture(params=["qdrant", "pgvector"])
def unreachable(request: pytest.FixtureRequest) -> Iterator[VectorStore]:
    yield _unreachable_qdrant() if request.param == "qdrant" else _unreachable_pgvector()


def _point(
    entity_id: int,
    vector: list[float],
    *,
    entity_type: RetrievalEntityType = PERSON,
    model: str = MODEL,
    content_hash: str = "hash",
) -> VectorPoint:
    return VectorPoint(
        document=SemanticDocument(
            entity_type=entity_type,
            entity_id=entity_id,
            text=f"entity {entity_id}",
            representation_version=1,
            content_hash=content_hash,
        ),
        vector=vector,
        embedding_model_id=model,
    )


def _ids(store: VectorStore, vector: list[float], **kwargs: object) -> list[int]:
    matches = store.search(NAME, vector, embedding_model_id=MODEL, **kwargs)  # type: ignore[arg-type]
    return [match.entity_id for match in sorted(matches, key=lambda m: (-m.score, m.entity_id))]


def _seed(store: VectorStore) -> None:
    store.ensure_collection(NAME, 3)
    store.upsert(
        NAME,
        [
            _point(1, [1.0, 0.0, 0.0]),
            _point(2, [0.9, 0.1, 0.0]),
            _point(3, [0.5, 0.5, 0.0]),
            _point(4, [0.0, 1.0, 0.0]),
            _point(5, [0.0, 0.0, 1.0]),
        ],
    )


def test_a_new_collection_is_empty_and_searchable(store: VectorStore) -> None:
    store.ensure_collection(NAME, 3)
    store.ensure_collection(NAME, 3)  # idempotent

    assert store.count(NAME) == 0
    assert _ids(store, [1.0, 0.0, 0.0], limit=10) == []


def test_upsert_adds_and_counts_points(store: VectorStore) -> None:
    _seed(store)

    assert store.count(NAME) == 5


def test_a_repeated_upsert_overwrites_the_same_entity(store: VectorStore) -> None:
    store.ensure_collection(NAME, 3)
    store.upsert(NAME, [_point(1, [1.0, 0.0, 0.0])])
    store.upsert(NAME, [_point(1, [0.0, 1.0, 0.0], content_hash="changed")])

    assert store.count(NAME) == 1
    [match] = store.search(NAME, [0.0, 1.0, 0.0], embedding_model_id=MODEL, limit=5)
    assert match.entity_id == 1 and match.score == pytest.approx(1.0, abs=1e-5)


def test_the_same_entity_id_of_two_types_is_two_points(store: VectorStore) -> None:
    store.ensure_collection(NAME, 3)
    store.upsert(NAME, [_point(1, [1.0, 0.0, 0.0]), _point(1, [0.0, 1.0, 0.0], entity_type=EVENT)])

    assert store.count(NAME) == 2


def test_search_ranks_by_cosine_similarity_higher_is_closer(store: VectorStore) -> None:
    _seed(store)

    matches = store.search(NAME, [2.0, 0.0, 0.0], embedding_model_id=MODEL, limit=5)
    scores = {match.entity_id: match.score for match in matches}

    assert _ids(store, [2.0, 0.0, 0.0], limit=5) == [1, 2, 3, 4, 5]
    # Cosine, not dot product or distance: the query's length does not matter.
    assert scores[1] == pytest.approx(1.0, abs=1e-5)
    assert scores[2] == pytest.approx(0.9 / math.sqrt(0.82), abs=1e-5)
    assert scores[3] == pytest.approx(0.5 / math.sqrt(0.5), abs=1e-5)
    assert scores[4] == pytest.approx(0.0, abs=1e-5)


def test_search_returns_at_most_limit(store: VectorStore) -> None:
    _seed(store)

    assert _ids(store, [1.0, 0.0, 0.0], limit=2) == [1, 2]


def test_entity_ids_restrict_the_candidates(store: VectorStore) -> None:
    _seed(store)

    assert _ids(store, [1.0, 0.0, 0.0], limit=10, entity_ids=[5, 3, 4]) == [3, 4, 5]
    assert _ids(store, [1.0, 0.0, 0.0], limit=1, entity_ids=[5, 3, 4]) == [3]
    assert _ids(store, [1.0, 0.0, 0.0], limit=10, entity_ids=[99]) == []


def test_delete_removes_only_the_given_entities_of_the_type(store: VectorStore) -> None:
    _seed(store)
    store.upsert(NAME, [_point(1, [1.0, 0.0, 0.0], entity_type=EVENT)])

    store.delete(NAME, PERSON, [1, 2])
    store.delete(NAME, PERSON, [])

    assert store.count(NAME) == 4  # persons 3, 4, 5 and the event 1
    assert _ids(store, [1.0, 0.0, 0.0], limit=10, entity_ids=[1, 2]) == [1]


def test_delete_in_a_missing_collection_does_nothing(store: VectorStore) -> None:
    store.delete("contract_missing", PERSON, [1])


def test_recreate_drops_the_points_and_can_change_the_size(store: VectorStore) -> None:
    _seed(store)

    store.recreate_collection(NAME, 4)

    assert store.count(NAME) == 0
    store.upsert(NAME, [_point(1, [0.0, 0.0, 0.0, 1.0])])
    assert _ids(store, [0.0, 0.0, 0.0, 1.0], limit=5) == [1]


def test_a_vector_size_mismatch_is_an_error_not_an_empty_result(store: VectorStore) -> None:
    _seed(store)

    with pytest.raises(VectorSizeMismatchError):
        store.ensure_collection(NAME, 4)
    with pytest.raises(VectorSizeMismatchError):
        store.search(NAME, [1.0, 0.0], embedding_model_id=MODEL, limit=5)


def test_an_index_of_another_embedding_model_is_refused(store: VectorStore) -> None:
    _seed(store)
    store.upsert(NAME, [_point(6, [1.0, 0.0, 0.0], model=OTHER_MODEL)])

    with pytest.raises(IndexModelMismatchError):
        store.check_embedding_model(NAME, MODEL)
    with pytest.raises(IndexModelMismatchError):
        store.search(NAME, [1.0, 0.0, 0.0], embedding_model_id=MODEL, limit=5)
    # Scanning the whole collection: the foreign point is not the best match of a query.
    store.upsert(NAME, [_point(6, [0.0, 0.0, 1.0], model=OTHER_MODEL)])
    with pytest.raises(IndexModelMismatchError):
        store.check_embedding_model(NAME, MODEL)


def test_an_empty_or_missing_collection_passes_the_model_check(store: VectorStore) -> None:
    store.check_embedding_model("contract_missing", MODEL)
    store.ensure_collection(NAME, 3)
    store.check_embedding_model(NAME, MODEL)


def test_a_missing_collection_is_unavailable_not_empty(store: VectorStore) -> None:
    assert store.count("contract_missing") == 0
    with pytest.raises(RetrievalUnavailableError):
        store.search("contract_missing", [1.0, 0.0, 0.0], embedding_model_id=MODEL, limit=5)


def test_an_unreachable_store_is_retrieval_unavailable(unreachable: VectorStore) -> None:
    with pytest.raises(RetrievalUnavailableError):
        unreachable.search(NAME, [1.0, 0.0, 0.0], embedding_model_id=MODEL, limit=5)
    with pytest.raises(RetrievalUnavailableError):
        unreachable.ensure_collection(NAME, 3)
