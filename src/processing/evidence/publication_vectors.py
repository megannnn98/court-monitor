"""Windows of the stored publications as vectors: the index the investigation's dense
retrieval searches (ADR 0027).

Derived and rebuildable: an article's text is cut into overlapping windows, each
embedded with the title before it as a passage by the configured model and stored in
`publication_vectors` with its offsets in the text. An article whose text has not changed since its windows were
made is skipped; a changed one is cut again. Run on demand (`index-publications`),
never by the scheduled pipeline: the model needs the optional `semantic` group.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

logger = logging.getLogger("entities")

WINDOW = 1_200
STRIDE = 1_000
BATCH = 64


class PassageEmbedder(Protocol):
    @property
    def model_id(self) -> str: ...

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        """Normalized vectors of passages, one per text."""
        ...


@dataclass
class VectorIndexResult:
    articles: int = 0
    indexed: int = 0
    unchanged: int = 0
    windows: int = 0


def windows_of(body: str) -> list[tuple[int, int]]:
    """Overlapping windows of the text: [start, end) offsets."""
    if not body:
        return []
    starts = range(0, max(len(body) - WINDOW, 0) + 1, STRIDE)
    found = [(start, min(start + WINDOW, len(body))) for start in starts]
    if found[-1][1] < len(body):
        found.append((max(len(body) - WINDOW, 0), len(body)))
    return found


def vector_literal(vector: Sequence[float]) -> str:
    return "[" + ",".join(f"{value:.6f}" for value in vector) + "]"


_ARTICLES = text(
    """
    SELECT a.id, coalesce(a.title, '') AS title, coalesce(a.text, '') AS text,
           (SELECT min(v.content_hash) FROM publication_vectors v
            WHERE v.article_id = a.id AND v.model_id = :model) AS indexed_hash
    FROM parsed_articles a
    ORDER BY a.id
    """
)
_DELETE = text("DELETE FROM publication_vectors WHERE article_id = :article AND model_id = :model")
_INSERT = text(
    """
    INSERT INTO publication_vectors
        (article_id, model_id, window_start, window_end, content_hash, embedding)
    VALUES (:article, :model, :start, :end, :hash, CAST(:embedding AS vector))
    """
)


class PublicationVectorIndexer:
    def __init__(self, session_factory: sessionmaker[Session], embedder: PassageEmbedder) -> None:
        self._session_factory = session_factory
        self._embedder = embedder

    def run(self) -> VectorIndexResult:
        model = self._embedder.model_id
        result = VectorIndexResult()
        with self._session_factory() as session:
            rows = session.execute(_ARTICLES, {"model": model}).all()
        for row in rows:
            result.articles += 1
            body = row.text
            digest = hashlib.sha256(f"{row.title}\n{body}".encode()).hexdigest()
            if row.indexed_hash == digest:
                result.unchanged += 1
                continue
            spans = windows_of(body)
            vectors: list[list[float]] = []
            for offset in range(0, len(spans), BATCH):
                batch = spans[offset : offset + BATCH]
                vectors.extend(
                    self._embedder.embed_documents(
                        [f"{row.title}\n{body[start:end]}" for start, end in batch]
                    )
                )
            with self._session_factory.begin() as session:
                session.execute(_DELETE, {"article": row.id, "model": model})
                if spans:
                    session.execute(
                        _INSERT,
                        [
                            {
                                "article": row.id,
                                "model": model,
                                "start": start,
                                "end": end,
                                "hash": digest,
                                "embedding": vector_literal(vector),
                            }
                            for (start, end), vector in zip(spans, vectors, strict=True)
                        ],
                    )
            result.indexed += 1
            result.windows += len(spans)
        logger.info("event=publication_vectors_indexed model=%s %s", model, result)
        return result
