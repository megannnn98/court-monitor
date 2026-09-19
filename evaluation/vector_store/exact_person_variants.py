"""Experiment: exact PERSON search in pgvector — where its time goes (ADR 0018).

Run on the pgvector copy left by person_search_modes.py in its exact build (no HNSW
index on the person collection). Compares three exact queries over the same rows:

    C1  the mode C query: collection AND vector_dims(embedding) = N (detoasts twice)
    C2  collection only: one detoast per row
    C3  C2 after `ALTER COLUMN embedding SET STORAGE PLAIN` and a table rewrite, so the
        3 KB vectors sit in the heap instead of TOAST (copy only: a schema change)

Each: one warm-up pass, five timed passes over the 45 person queries, EXPLAIN (ANALYZE,
BUFFERS), and the result checked equal to C1's.
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path
from typing import Any

from sqlalchemy import text
from validate_real_corpus import QUERIES

from db.database import create_database_engine, create_session_factory
from semantic_retrieval.embeddings import EmbeddingConfig, SentenceTransformerEmbedder
from semantic_retrieval.pgvector_store import _vector_literal

COLLECTION = "persons_semantic"
QUERIES_SQL = {
    "C1_with_vector_dims": (
        f"SELECT entity_id FROM semantic_vectors WHERE collection_name = '{COLLECTION}' "
        "AND vector_dims(embedding) = 768 "
        "ORDER BY embedding <=> CAST(:q AS vector), entity_id LIMIT 100"
    ),
    "C2_collection_only": (
        f"SELECT entity_id FROM semantic_vectors WHERE collection_name = '{COLLECTION}' "
        "ORDER BY embedding <=> CAST(:q AS vector), entity_id LIMIT 100"
    ),
}


def _measure(session_factory: Any, sql: str, vectors: list[str]) -> dict[str, Any]:
    results: list[list[int]] = []
    with session_factory() as session:
        for vector in vectors:  # warm-up
            session.execute(text(sql), {"q": vector}).all()
        samples: list[float] = []
        for _ in range(5):
            for vector in vectors:
                started = time.perf_counter()
                session.execute(text(sql), {"q": vector}).all()
                samples.append((time.perf_counter() - started) * 1000)
        for vector in vectors:
            results.append(list(session.execute(text(sql), {"q": vector}).scalars()))
        plan = [
            line.strip()[:160]
            for line in session.execute(
                text("EXPLAIN (ANALYZE, BUFFERS) " + sql), {"q": vectors[0]}
            ).scalars()
            if "::vector" not in line
        ]
    samples.sort()
    return {
        "latency_ms": {
            "n": len(samples),
            "p50": round(statistics.median(samples), 2),
            "p95": round(samples[round(0.95 * len(samples)) - 1], 2),
            "max": round(samples[-1], 2),
        },
        "uses_hnsw": any("hnsw" in line.lower() for line in plan),
        "plan": plan,
        "results": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    engine = create_database_engine(args.database_url)
    session_factory = create_session_factory(engine)
    embedder = SentenceTransformerEmbedder(EmbeddingConfig())
    queries = [
        q
        for q in json.loads(QUERIES.read_text(encoding="utf-8"))["queries"]
        if q["entity_type"] == "person"
    ]
    vectors = [_vector_literal(embedder.embed_query(q["text"])) for q in queries]
    out: dict[str, Any] = {}
    for name, sql in QUERIES_SQL.items():
        out[name] = _measure(session_factory, sql, vectors)
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        started = time.perf_counter()
        connection.execute(
            text("ALTER TABLE semantic_vectors ALTER COLUMN embedding SET STORAGE PLAIN")
        )
        connection.execute(text("VACUUM FULL ANALYZE semantic_vectors"))
        rewrite = time.perf_counter() - started
        size = connection.execute(
            text("SELECT pg_total_relation_size('semantic_vectors')")
        ).scalar_one()
    out["C3_plain_storage"] = _measure(session_factory, QUERIES_SQL["C2_collection_only"], vectors)
    out["C3_plain_storage"]["rewrite_seconds"] = round(rewrite, 1)
    out["C3_plain_storage"]["table_total_bytes"] = int(size)
    reference = out["C1_with_vector_dims"]["results"]
    summary = {}
    for name, variant in out.items():
        variant["same_results_as_C1"] = variant["results"] == reference
        del variant["results"]
        summary[name] = {k: variant[k] for k in ("latency_ms", "uses_hnsw", "same_results_as_C1")}
    args.output.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
