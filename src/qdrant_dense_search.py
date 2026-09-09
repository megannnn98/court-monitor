from qdrant_client import QdrantClient

from models import SearchHit, SearchQuery
from text_embedder import TextEmbedder


class QdrantDenseSearch:
    def __init__(
        self,
        client: QdrantClient,
        collection_name: str,
        embedder: TextEmbedder,
    ) -> None:
        self._client = client
        self._collection_name = collection_name
        self._embedder = embedder

    def search(self, query: SearchQuery) -> list[SearchHit]:
        vector = self._embedder.embed_query(query.text)

        result = self._client.query_points(
            collection_name=self._collection_name,
            query=vector,
            limit=query.limit,
            with_payload=True,
        )

        hits: list[SearchHit] = []

        for point in result.points:
            payload = point.payload

            if payload is None:
                continue

            hits.append(
                SearchHit(
                    chunk_id=int(payload["chunk_id"]),
                    article_id=int(payload["article_id"]),
                    source_base_url=str(payload["source_base_url"]),
                    external_id=str(payload["external_id"]),
                    ordinal=int(payload["ordinal"]),
                    title=str(payload["title"]),
                    published_at=payload["published_at"],
                    url=str(payload["url"]),
                    text=str(payload["text"]),
                    score=float(point.score),
                )
            )

        return hits
