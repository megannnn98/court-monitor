"""Dense search of the stored publications: the query's vector against the windows of
`publication_vectors` (pgvector, cosine distance, exact). An article ranks by its best
window. Read only.

Exact search reads every window of the model: at 5 000 windows a few milliseconds.
An HNSW index is worth it far beyond that (`EXPLAIN` before adding one, ADR 0027); it
would have to keep this meaning — the best documents, not the best windows.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.orm import Session

from results.search import RankedPublication


def _literal(vector: Sequence[float]) -> str:
    return "[" + ",".join(f"{value:.6f}" for value in vector) + "]"


def dense_publications(
    session: Session,
    vector: Sequence[float],
    *,
    model_id: str,
    limit: int,
    published_from: datetime | None = None,
    published_to: datetime | None = None,
) -> list[RankedPublication]:
    where = ["v.model_id = :model"]
    params: dict[str, object] = {
        "model": model_id,
        "vector": _literal(vector),
        "limit": limit,
    }
    if published_from is not None:
        where.append("a.published_at >= :published_from")
        params["published_from"] = published_from
    if published_to is not None:
        where.append("a.published_at <= :published_to")
        params["published_to"] = published_to
    statement = text(
        f"""
        WITH best AS (
            -- Each article by its nearest window, before any limit: a limit on windows
            -- first would let one long article take them all and hide the next ones.
            SELECT DISTINCT ON (v.article_id) v.article_id, v.window_start, v.window_end,
                   v.embedding <=> CAST(:vector AS vector) AS distance
            FROM publication_vectors v
            JOIN parsed_articles a ON a.id = v.article_id
            WHERE {" AND ".join(where)}
            ORDER BY v.article_id, distance
        )
        SELECT b.article_id, a.title, d.canonical_url, s.name AS source, a.published_at,
               substr(a.text, b.window_start + 1, b.window_end - b.window_start) AS quote,
               b.window_start, b.window_end, 1 - b.distance AS similarity
        FROM best b
        JOIN parsed_articles a ON a.id = b.article_id
        JOIN source_documents d ON d.id = a.document_id
        JOIN sources s ON s.id = d.source_id
        ORDER BY b.distance, b.article_id
        LIMIT :limit
        """
    )
    return [
        RankedPublication(
            article_id=row.article_id,
            title=row.title or "",
            url=row.canonical_url,
            source=row.source,
            published_at=row.published_at,
            quote=row.quote or "",
            span_start=row.window_start,
            span_end=row.window_end,
            rank=float(row.similarity),
        )
        for row in session.execute(statement, params)
    ]
