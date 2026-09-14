"""QdrantVectorStore on qdrant-client local mode (no Qdrant service needed)."""

from __future__ import annotations

import httpx
import pytest
from qdrant_client import QdrantClient
from semantic_fakes import document

from semantic_retrieval.models import (
    RetrievalEntityType,
    RetrievalUnavailableError,
    VectorSizeMismatchError,
)
from semantic_retrieval.vector_store import QdrantVectorStore, VectorPoint, point_id

PERSON = RetrievalEntityType.PERSON
COLLECTION = "persons_semantic"
PINNED_PERSON_1 = "726af293-9bdf-5a2a-9761-161f68e624c1"


def _store() -> tuple[QdrantVectorStore, QdrantClient]:
    client = QdrantClient(":memory:")
    store = QdrantVectorStore(client)
    store.ensure_collection(COLLECTION, 3)
    return store, client


def test_point_id_is_deterministic_and_type_specific() -> None:
    assert point_id(PERSON, 7) == point_id(PERSON, 7)
    assert point_id(PERSON, 7) != point_id(RetrievalEntityType.EVENT, 7)
    assert point_id(PERSON, 7) != point_id(PERSON, 8)
    # Pinned: changing the namespace would orphan every existing point.
    assert point_id(PERSON, 1) == PINNED_PERSON_1


def test_repeated_upsert_overwrites_the_same_point_with_minimal_payload() -> None:
    store, client = _store()

    store.upsert(COLLECTION, [VectorPoint(document(1, "старый"), [1.0, 0.0, 0.0])])
    store.upsert(COLLECTION, [VectorPoint(document(1, "новый"), [0.0, 1.0, 0.0])])

    assert store.count(COLLECTION) == 1
    (record,) = client.retrieve(COLLECTION, ids=[point_id(PERSON, 1)], with_payload=True)
    assert record.payload == {
        "entity_id": 1,
        "entity_type": "person",
        "representation_version": 1,
        "content_hash": document(1, "новый").content_hash,
    }
    (match,) = store.search(COLLECTION, [0.0, 1.0, 0.0], limit=5)
    assert match.entity_id == 1 and match.score == pytest.approx(1.0)


def test_search_orders_by_similarity_limits_and_filters() -> None:
    store, _ = _store()
    store.upsert(
        COLLECTION,
        [
            VectorPoint(document(1, "a"), [1.0, 0.0, 0.0]),
            VectorPoint(document(2, "b"), [0.9, 0.1, 0.0]),
            VectorPoint(document(3, "c"), [0.0, 0.0, 1.0]),
        ],
    )

    top = store.search(COLLECTION, [1.0, 0.0, 0.0], limit=2)
    filtered = store.search(COLLECTION, [1.0, 0.0, 0.0], limit=5, entity_ids=[3])

    assert [match.entity_id for match in top] == [1, 2]
    assert [match.entity_id for match in filtered] == [3]


def test_delete_removes_points_and_tolerates_missing_collection() -> None:
    store, _ = _store()
    store.upsert(COLLECTION, [VectorPoint(document(1, "a"), [1.0, 0.0, 0.0])])

    store.delete(COLLECTION, PERSON, [1, 99])
    store.delete("missing_collection", PERSON, [1])

    assert store.count(COLLECTION) == 0


def test_vector_size_mismatch_is_an_error_not_an_empty_result() -> None:
    store, _ = _store()

    with pytest.raises(VectorSizeMismatchError):
        store.search(COLLECTION, [1.0, 0.0], limit=5)
    with pytest.raises(VectorSizeMismatchError):
        store.ensure_collection(COLLECTION, 768)


def test_missing_collection_is_unavailable_not_empty() -> None:
    store = QdrantVectorStore(QdrantClient(":memory:"))

    with pytest.raises(RetrievalUnavailableError, match="rebuild-semantic-index"):
        store.search("persons_semantic", [1.0], limit=5)


def test_recreate_collection_drops_points_and_changes_size() -> None:
    store, _ = _store()
    store.upsert(COLLECTION, [VectorPoint(document(1, "a"), [1.0, 0.0, 0.0])])

    store.recreate_collection(COLLECTION, 2)

    assert store.count(COLLECTION) == 0
    store.ensure_collection(COLLECTION, 2)


def test_unreachable_qdrant_raises_retrieval_unavailable() -> None:
    class RefusingClient(QdrantClient):
        def collection_exists(self, collection_name: str, **kwargs: object) -> bool:
            raise httpx.ConnectError("connection refused")

    store = QdrantVectorStore(RefusingClient(":memory:"))

    with pytest.raises(RetrievalUnavailableError):
        store.search(COLLECTION, [1.0, 0.0, 0.0], limit=5)


def test_connection_refused_by_a_real_client_is_retrieval_unavailable() -> None:
    store = QdrantVectorStore(
        QdrantClient(url="http://127.0.0.1:1", timeout=2, check_compatibility=False)
    )

    with pytest.raises(RetrievalUnavailableError):
        store.search(COLLECTION, [1.0, 0.0, 0.0], limit=5)
