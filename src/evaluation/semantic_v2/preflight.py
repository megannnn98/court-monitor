"""Preflight of an embedding model before the Semantic Retrieval v2 benchmark.

Loads the model through the production embedder (its EmbeddingProfile included) and
measures on real semantic documents: dimension, pooling, VRAM peak, document throughput
per batch size, query latency. Then writes its vectors to pgvector the way production
would (PERSON exact, EVENT HNSW, PLAIN storage) in a disposable database and checks that
both searches work and no vector is stored out of line.

    PYTHONPATH=src uv run python -m evaluation.semantic_v2.preflight \\
        --documents-database-url <copy> --pgvector-database-url <..._test> --model BAAI/bge-m3
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import time
from pathlib import Path
from typing import Any

import torch
from sqlalchemy import text

from db.database import create_database_engine, create_session_factory
from db.maintenance import require_disposable_database, truncate_disposable_tables
from semantic_retrieval.embeddings import (
    EmbeddingConfig,
    SentenceTransformerEmbedder,
    embedding_profile,
)
from semantic_retrieval.factory import SemanticRetrievalConfig, create_configured_vector_store
from semantic_retrieval.models import RetrievalEntityType, SemanticDocument
from semantic_retrieval.vector_store import VectorPoint

SAMPLE = 2000
BATCH_SIZES = (16, 32, 64)
QUERIES = [
    "журналист, задержанный на митинге",
    "приговор за антивоенные посты",
    "госизмена перевод денег",
    "обыск у активиста",
    "Свидетели Иеговы",
]


def _documents(url: str) -> dict[RetrievalEntityType, list[SemanticDocument]]:
    engine = create_database_engine(url)
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT entity_type, entity_id, text, representation_version, content_hash "
                "FROM semantic_documents ORDER BY entity_type, entity_id"
            )
        ).all()
    engine.dispose()
    by_type: dict[RetrievalEntityType, list[SemanticDocument]] = {
        t: [] for t in RetrievalEntityType
    }
    for kind, entity_id, body, version, content_hash in rows:
        by_type[RetrievalEntityType(kind)].append(
            SemanticDocument(
                entity_type=RetrievalEntityType(kind),
                entity_id=entity_id,
                text=body,
                representation_version=version,
                content_hash=content_hash,
            )
        )
    rng = random.Random(20260920)
    return {kind: rng.sample(docs, min(SAMPLE, len(docs))) for kind, docs in by_type.items()}


def _ms(samples: list[float]) -> dict[str, float]:
    ordered = sorted(s * 1000 for s in samples)
    return {
        "p50": round(statistics.median(ordered), 2),
        "p95": round(ordered[round(0.95 * len(ordered)) - 1], 2),
        "max": round(ordered[-1], 2),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--documents-database-url", required=True)
    parser.add_argument("--pgvector-database-url", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    documents = _documents(args.documents_database_url)
    texts = [d.text for docs in documents.values() for d in docs]
    report: dict[str, Any] = {
        "model": args.model,
        "profile": embedding_profile(args.model).__dict__,
        "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
        "documents_sampled": len(texts),
    }

    torch.cuda.reset_peak_memory_stats()
    started = time.perf_counter()
    embedder = SentenceTransformerEmbedder(EmbeddingConfig(model_id=args.model, device="cuda"))
    report["dimension"] = embedder.dimension
    report["load_seconds"] = round(time.perf_counter() - started, 1)
    model: Any = embedder._load()
    report["pooling"] = str(model[1]) if len(model) > 1 else None
    report["max_seq_length"] = model.max_seq_length
    report["vram_after_load_mb"] = round(torch.cuda.memory_allocated() / 2**20)

    throughput = {}
    for batch_size in BATCH_SIZES:
        timed = SentenceTransformerEmbedder(
            EmbeddingConfig(model_id=args.model, device="cuda", batch_size=batch_size)
        )
        timed._model, timed._dimension = model, embedder.dimension  # one loaded model
        torch.cuda.reset_peak_memory_stats()
        timed.embed_documents(texts[:64])  # warm-up
        started = time.perf_counter()
        timed.embed_documents(texts)
        seconds = time.perf_counter() - started
        throughput[str(batch_size)] = {
            "documents_per_second": round(len(texts) / seconds, 1),
            "vram_peak_mb": round(torch.cuda.max_memory_allocated() / 2**20),
        }
    report["document_throughput"] = throughput

    for query in QUERIES:
        embedder.embed_query(query)
    samples = []
    for _ in range(20):
        for query in QUERIES:
            started = time.perf_counter()
            embedder.embed_query(query)
            samples.append(time.perf_counter() - started)
    report["query_latency_ms"] = _ms(samples)

    engine = create_database_engine(args.pgvector_database_url)
    require_disposable_database(engine)
    truncate_disposable_tables(engine)
    config = SemanticRetrievalConfig(vector_backend="pgvector")
    store = create_configured_vector_store(config, create_session_factory(engine))
    vectors = {
        kind: embedder.embed_documents([d.text for d in docs]) for kind, docs in documents.items()
    }
    checks: dict[str, Any] = {}
    for kind, collection in config.collections.items():
        store.recreate_collection(collection, embedder.dimension)
        store.upsert(
            collection,
            [
                VectorPoint(document=d, vector=v, embedding_model_id=args.model)
                for d, v in zip(documents[kind], vectors[kind], strict=True)
            ],
        )
        query_vector = embedder.embed_query(QUERIES[0])
        matches = store.search(collection, query_vector, embedding_model_id=args.model, limit=100)
        checks[kind.value] = {"stored": store.count(collection), "top100_returned": len(matches)}
    with engine.connect() as connection:
        checks["hnsw_indexes"] = list(
            connection.execute(
                text(
                    "SELECT indexname FROM pg_indexes WHERE tablename = 'semantic_vectors' "
                    "AND indexname LIKE :p"
                ),
                {"p": f"ix_semvec_hnsw_%_{embedder.dimension}"},
            ).scalars()
        )
        checks["rows_out_of_line"] = connection.execute(
            text(
                "SELECT count(*) FROM semantic_vectors "
                "WHERE pg_column_toast_chunk_id(embedding) IS NOT NULL"
            )
        ).scalar_one()
        checks["vector_bytes"] = connection.execute(
            text("SELECT max(pg_column_size(embedding)) FROM semantic_vectors")
        ).scalar_one()
    truncate_disposable_tables(engine)
    report["pgvector"] = checks
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
