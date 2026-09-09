from datetime import datetime
from typing import Any

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from orm_models import (
    ArticleChunkRecord,
    ParsedArticleRecord,
    Source,
    SourceDocument,
)
from text_embedder import TextEmbedder


def make_payload(
    *,
    chunk_id: int,
    article_id: int,
    source_base_url: str,
    external_id: str,
    ordinal: int,
    title: str,
    published_at: datetime | None,
    url: str,
    text: str,
) -> dict[str, Any]:
    return {
        "chunk_id": chunk_id,
        "article_id": article_id,
        "source_base_url": source_base_url,
        "external_id": external_id,
        "ordinal": ordinal,
        "title": title,
        "published_at": published_at.isoformat() if published_at is not None else None,
        "url": url,
        "text": text,
    }


class QdrantChunkIndexer:
    def __init__(
        self,
        client: QdrantClient,
        collection_name: str,
        vector_size: int,
        session_factory: sessionmaker[Session],
        embedder: TextEmbedder,
    ) -> None:
        self._client = client
        self._collection_name = collection_name
        self._vector_size = vector_size
        self._session_factory = session_factory
        self._embedder = embedder

    def ensure_collection(self) -> None:
        if self._client.collection_exists(self._collection_name):
            return

        self._client.create_collection(
            collection_name=self._collection_name,
            vectors_config=VectorParams(
                size=self._vector_size,
                distance=Distance.COSINE,
            ),
        )

    def upsert_chunk(
        self,
        *,
        chunk_id: int,
        vector: list[float],
        payload: dict[str, Any],
    ) -> None:
        self._client.upsert(
            collection_name=self._collection_name,
            points=[
                PointStruct(
                    id=chunk_id,
                    vector=vector,
                    payload=payload,
                )
            ],
        )

    def index_all(self) -> None:
        with self._session_factory() as session:
            rows = (
                session.execute(
                    select(
                        ArticleChunkRecord.id.label("chunk_id"),
                        ParsedArticleRecord.id.label("article_id"),
                        Source.base_url.label("source_base_url"),
                        SourceDocument.external_id,
                        ArticleChunkRecord.ordinal,
                        ParsedArticleRecord.title,
                        ParsedArticleRecord.published_at,
                        SourceDocument.canonical_url.label("url"),
                        ArticleChunkRecord.text,
                    )
                    .join(
                        ParsedArticleRecord,
                        ArticleChunkRecord.parsed_article_id == ParsedArticleRecord.id,
                    )
                    .join(
                        SourceDocument,
                        ParsedArticleRecord.document_id == SourceDocument.id,
                    )
                    .join(
                        Source,
                        SourceDocument.source_id == Source.id,
                    )
                    .order_by(ArticleChunkRecord.id)
                )
                .mappings()
                .all()
            )

        texts = [row["text"] for row in rows]
        vectors = self._embedder.embed_documents(texts)

        points = []

        for row, vector in zip(rows, vectors, strict=True):
            points.append(
                PointStruct(
                    id=row["chunk_id"],
                    vector=vector,
                    payload=make_payload(
                        chunk_id=row["chunk_id"],
                        article_id=row["article_id"],
                        source_base_url=row["source_base_url"],
                        external_id=row["external_id"],
                        ordinal=row["ordinal"],
                        title=row["title"],
                        published_at=row["published_at"],
                        url=row["url"],
                        text=row["text"],
                    ),
                )
            )

        if not points:
            return

        self._client.upsert(
            collection_name=self._collection_name,
            points=points,
        )

    def recreate_collection(self) -> None:
        if self._client.collection_exists(self._collection_name):
            self._client.delete_collection(self._collection_name)

        self._client.create_collection(
            collection_name=self._collection_name,
            vectors_config=VectorParams(
                size=self._vector_size,
                distance=Distance.COSINE,
            ),
        )
