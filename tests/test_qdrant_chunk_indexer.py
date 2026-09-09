from datetime import UTC, datetime
from unittest.mock import Mock

from qdrant_client import QdrantClient
from sqlalchemy.orm import Session, sessionmaker

from qdrant_chunk_indexer import QdrantChunkIndexer, make_payload
from text_embedder import TextEmbedder


def test_ensure_collection_creates_missing_collection() -> None:
    client = Mock(spec=QdrantClient)
    client.collection_exists.return_value = False

    indexer = QdrantChunkIndexer(
        client=client,
        collection_name="article_chunks_dense",
        vector_size=768,
        session_factory=Mock(spec=sessionmaker[Session]),
        embedder=Mock(spec=TextEmbedder),
    )

    indexer.ensure_collection()

    client.create_collection.assert_called_once()


def test_ensure_collection_does_not_recreate_existing_collection() -> None:
    client = Mock(spec=QdrantClient)
    client.collection_exists.return_value = True

    indexer = QdrantChunkIndexer(
        client=client,
        collection_name="article_chunks_dense",
        vector_size=768,
        session_factory=Mock(spec=sessionmaker[Session]),
        embedder=Mock(spec=TextEmbedder),
    )

    indexer.ensure_collection()

    client.create_collection.assert_not_called()


def test_upsert_chunk() -> None:
    client = Mock(spec=QdrantClient)

    indexer = QdrantChunkIndexer(
        client=client,
        collection_name="article_chunks_dense",
        vector_size=768,
        session_factory=Mock(spec=sessionmaker[Session]),
        embedder=Mock(spec=TextEmbedder),
    )

    indexer.upsert_chunk(
        chunk_id=42,
        vector=[0.1, 0.2, 0.3],
        payload={
            "article_id": 10,
            "text": "Текст чанка",
        },
    )

    client.upsert.assert_called_once()

    call = client.upsert.call_args

    assert call.kwargs["collection_name"] == "article_chunks_dense"

    point = call.kwargs["points"][0]
    assert point.id == 42
    assert point.vector == [0.1, 0.2, 0.3]
    assert point.payload == {
        "article_id": 10,
        "text": "Текст чанка",
    }


def test_make_payload() -> None:
    published_at = datetime(2026, 8, 13, 17, 57, tzinfo=UTC)

    payload = make_payload(
        chunk_id=42,
        article_id=10,
        source_base_url="https://ovd.info",
        external_id="rehabilitation-nazism",
        ordinal=1,
        title="Заголовок",
        published_at=published_at,
        url="https://ovd.info/example",
        text="Текст чанка",
    )

    assert payload == {
        "chunk_id": 42,
        "article_id": 10,
        "source_base_url": "https://ovd.info",
        "external_id": "rehabilitation-nazism",
        "ordinal": 1,
        "title": "Заголовок",
        "published_at": "2026-08-13T17:57:00+00:00",
        "url": "https://ovd.info/example",
        "text": "Текст чанка",
    }


def test_recreate_collection_deletes_existing_collection() -> None:
    client = Mock(spec=QdrantClient)
    client.collection_exists.return_value = True

    indexer = QdrantChunkIndexer(
        client=client,
        collection_name="article_chunks_dense",
        vector_size=768,
        session_factory=Mock(spec=sessionmaker[Session]),
        embedder=Mock(spec=TextEmbedder),
    )

    indexer.recreate_collection()

    client.delete_collection.assert_called_once_with("article_chunks_dense")
    client.create_collection.assert_called_once()


def test_recreate_collection_does_not_delete_missing_collection() -> None:
    client = Mock(spec=QdrantClient)
    client.collection_exists.return_value = False

    indexer = QdrantChunkIndexer(
        client=client,
        collection_name="article_chunks_dense",
        vector_size=768,
        session_factory=Mock(spec=sessionmaker[Session]),
        embedder=Mock(spec=TextEmbedder),
    )

    indexer.recreate_collection()

    client.delete_collection.assert_not_called()
    client.create_collection.assert_called_once()
