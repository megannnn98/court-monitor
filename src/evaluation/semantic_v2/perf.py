"""Semantic Retrieval v2: performance of one embedding model on the stand corpus.

Run once per model, each in its own process (peak RAM and VRAM are per process):

    PYTHONPATH=src uv run --group semantic python \
        -m evaluation.semantic_v2.perf --database-url ... --model BAAI/bge-m3

Measures on the same replayed documents (PERSON v2, EVENT v1): model load, full
corpus embedding (documents/s), full semantic rebuild into scratch pgvector
collections (embed + upsert + HNSW), a small incremental batch, query embedding
latency, dense retrieval latency and vector storage size. The embedder has no
inference cache; the scratch collections are dropped afterwards.
"""

from __future__ import annotations

import argparse
import math
import resource
import time
from statistics import median

from sqlalchemy import text

from db.database import create_database_engine, create_session_factory
from db.maintenance import require_disposable_database
from evaluation.real_world.retrieval_eval import load_retrieval_queries
from evaluation.semantic_v2.retrieval import REPORT_DIR, _write_json, runnable
from evaluation.semantic_v2.stand import (
    UPSERT_BATCH,
    collections_for,
    load_documents,
    model_slug,
    store_for,
)
from semantic_retrieval.embeddings import EmbeddingConfig, SentenceTransformerEmbedder
from semantic_retrieval.models import RetrievalEntityType, RetrievalQuery
from semantic_retrieval.pgvector_store import PgVectorStore
from semantic_retrieval.retrievers import QdrantEntityRetriever
from semantic_retrieval.vector_store import VectorPoint

INCREMENTAL_BATCH = 16


def _percentiles(values: list[float]) -> dict[str, float]:
    ordered = sorted(values)
    return {
        "p50": round(median(ordered), 2),
        "p95": round(ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)], 2),
        "n": len(ordered),
    }


def _vram_peak_mib() -> float | None:
    try:
        import torch
    except ImportError:
        return None
    if not torch.cuda.is_available():
        return None
    return round(float(torch.cuda.max_memory_allocated()) / 2**20, 1)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--model", required=True)
    args = parser.parse_args()
    engine = create_database_engine(args.database_url)
    require_disposable_database(engine)
    session_factory = create_session_factory(engine)
    slug = model_slug(args.model)
    documents = {
        entity_type: load_documents(session_factory, entity_type)
        for entity_type in RetrievalEntityType
    }
    total = sum(len(d) for d in documents.values())

    started = time.perf_counter()
    embedder = SentenceTransformerEmbedder(EmbeddingConfig(model_id=args.model))
    embedder.embed_query("прогрев")  # loads the model
    load_seconds = time.perf_counter() - started

    # Full rebuild into scratch collections: embed, upsert, HNSW (EVENT) / exact (PERSON).
    scratch = {t: f"sv2perf_{slug}_{t.value}s" for t in RetrievalEntityType}
    store = PgVectorStore(session_factory, exact_collections=[scratch[RetrievalEntityType.PERSON]])
    rebuild_started = time.perf_counter()
    embed_seconds = 0.0
    for entity_type, docs in documents.items():
        store.recreate_collection(scratch[entity_type], embedder.dimension)
        started = time.perf_counter()
        vectors = embedder.embed_documents([d.text for d in docs])
        embed_seconds += time.perf_counter() - started
        points = [
            VectorPoint(document=d, vector=v, embedding_model_id=args.model)
            for d, v in zip(docs, vectors, strict=True)
        ]
        for offset in range(0, len(points), UPSERT_BATCH):
            store.upsert(scratch[entity_type], points[offset : offset + UPSERT_BATCH])
    rebuild_seconds = time.perf_counter() - rebuild_started

    # Incremental: a small batch of changed EVENT documents (HNSW collection).
    batch = documents[RetrievalEntityType.EVENT][:INCREMENTAL_BATCH]
    started = time.perf_counter()
    vectors = embedder.embed_documents([d.text for d in batch])
    store.upsert(
        scratch[RetrievalEntityType.EVENT],
        [
            VectorPoint(document=d, vector=v, embedding_model_id=args.model)
            for d, v in zip(batch, vectors, strict=True)
        ],
    )
    incremental_seconds = time.perf_counter() - started

    with session_factory() as session:
        storage = {
            entity_type.value: session.execute(
                text(
                    "SELECT count(*), coalesce(sum(pg_column_size(embedding)), 0) "
                    "FROM semantic_vectors WHERE collection_name = :name"
                ),
                {"name": scratch[entity_type]},
            ).one()
            for entity_type in RetrievalEntityType
        }
        hnsw_bytes = session.execute(
            text(
                "SELECT coalesce(sum(pg_relation_size(indexrelid)), 0) FROM pg_index i "
                "JOIN pg_class c ON c.oid = i.indexrelid WHERE c.relname LIKE :pattern"
            ),
            {"pattern": f"ix_semvec_hnsw_{scratch[RetrievalEntityType.EVENT]}_%"},
        ).scalar_one()
        session.execute(
            text("DELETE FROM semantic_vector_collections WHERE name = ANY(:names)"),
            {"names": list(scratch.values())},
        )
        for (name,) in session.execute(
            text("SELECT indexname FROM pg_indexes WHERE indexname LIKE 'ix_semvec_hnsw_sv2perf_%'")
        ).all():
            session.execute(text(f'DROP INDEX IF EXISTS "{name}"'))
        session.commit()

    # Latency on the stand's own collections, dev + validation query texts.
    queries = runnable(load_retrieval_queries())
    query_ms: list[float] = []
    for query in queries:
        started = time.perf_counter()
        embedder.embed_query(query.text)
        query_ms.append((time.perf_counter() - started) * 1000)
    retriever = QdrantEntityRetriever(
        embedder=embedder,
        store=store_for(session_factory, args.model),
        collections=collections_for(args.model),
    )
    dense_ms: dict[str, list[float]] = {t.value: [] for t in RetrievalEntityType}
    for query in queries:
        started = time.perf_counter()
        retriever.retrieve(
            RetrievalQuery(text=query.text, entity_type=query.entity_type, limit=100)
        )
        dense_ms[query.entity_type.value].append((time.perf_counter() - started) * 1000)
    engine.dispose()

    report = {
        "embedding_model_id": args.model,
        "dimension": embedder.dimension,
        "documents": total,
        "model_load_seconds": round(load_seconds, 2),
        "full_corpus_embedding_seconds": round(embed_seconds, 2),
        "documents_per_second": round(total / embed_seconds, 1),
        "full_rebuild_seconds": round(rebuild_seconds, 2),
        "incremental_batch": {
            "documents": len(batch),
            "seconds": round(incremental_seconds, 3),
        },
        "query_embedding_ms": _percentiles(query_ms),
        "dense_retrieval_ms": {k: _percentiles(v) for k, v in dense_ms.items()},
        "vram_peak_mib": _vram_peak_mib(),
        # ru_maxrss is in KiB on Linux.
        "ram_peak_mib": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1),
        "vector_storage": {
            entity_type: {"vectors": int(count), "vector_bytes": int(size)}
            for entity_type, (count, size) in storage.items()
        },
        "event_hnsw_index_bytes": int(hnsw_bytes),
    }
    _write_json(REPORT_DIR / f"perf_{slug}.json", report)
    print(report)


if __name__ == "__main__":
    main()
