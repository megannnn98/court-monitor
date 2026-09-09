from unittest.mock import Mock

from qdrant_client import QdrantClient
from qdrant_client.models import ScoredPoint

from models import SearchQuery
from qdrant_dense_search import QdrantDenseSearch
from text_embedder import TextEmbedder


def test_dense_search_returns_search_hits() -> None:
    client = Mock(spec=QdrantClient)
    embedder = Mock(spec=TextEmbedder)

    embedder.embed_query.return_value = [0.1, 0.2]

    client.query_points.return_value.points = [
        ScoredPoint(
            id=42,
            version=0,
            score=0.91,
            payload={
                "chunk_id": 42,
                "article_id": 10,
                "source_base_url": "https://ovd.info",
                "external_id": "example",
                "ordinal": 0,
                "title": "Заголовок",
                "published_at": None,
                "url": "https://ovd.info/example",
                "text": "Текст чанка",
            },
            vector=None,
        )
    ]

    search = QdrantDenseSearch(
        client=client,
        collection_name="article_chunks_dense",
        embedder=embedder,
    )

    hits = search.search(
        SearchQuery(
            text="реабилитация нацизма",
            limit=3,
        )
    )

    assert len(hits) == 1
    assert hits[0].chunk_id == 42
    assert hits[0].score == 0.91
