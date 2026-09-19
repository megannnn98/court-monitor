"""Semantic Retrieval v2 stand: one corpus replay, one pgvector index per embedding model.

    replay  the cached corpus through the production monitoring pipeline into a
            disposable database (semantic documents: PERSON v2, EVENT v1, unchanged)
    index   embed those documents with one model into its own pgvector collections
            (PERSON exact, EVENT HNSW — ADR 0018), in the same disposable database

Both models retrieve over the same documents of the same replay. Nothing here
touches the production database, its semantic index or Qdrant.

    PYTHONPATH=src uv run python -m evaluation.semantic_v2.stand replay --database-url ...
    PYTHONPATH=src uv run python -m evaluation.semantic_v2.stand index --model BAAI/bge-m3 ...
"""

from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from db.database import create_database_engine, create_session_factory
from db.maintenance import require_disposable_database
from db.models.semantic import SemanticDocumentRecord
from evaluation.real_world.cli_helpers import corpus_texts
from evaluation.real_world.corpus_cache import RawCorpusCache
from evaluation.real_world.corpus_run import CorpusRunner
from evaluation.real_world.evaluator import (
    IN_MEMORY_QDRANT,
    REAL_WORLD_COLLECTIONS,
    EvaluationDataError,
    EvaluationInputs,
    default_rf_snapshot,
    validate_inputs,
)
from evaluation.real_world.golden import load_golden_dataset
from evaluation.real_world.models import DEFAULT_CACHE_DIR, DEFAULT_MANIFEST_PATH, load_manifest
from evaluation.real_world.monitoring_simulation import (
    run_temporal_simulation,
    semantic_indexer_factory,
)
from evaluation.real_world.policy import load_policy
from evaluation.real_world.state_snapshot import table_counts
from semantic_retrieval.documents import (
    EVENT_REPRESENTATION_VERSION,
    PERSON_REPRESENTATION_VERSION,
)
from semantic_retrieval.embeddings import EmbeddingConfig, SentenceTransformerEmbedder
from semantic_retrieval.models import RetrievalEntityType, SemanticDocument
from semantic_retrieval.pgvector_store import PgVectorStore
from semantic_retrieval.vector_store import VectorPoint

REPORT_DIR = Path("reports/semantic_retrieval_v2")
STAND_MANIFEST = REPORT_DIR / "stand.json"
UPSERT_BATCH = 256


# Short labels: pgvector collection names are limited to 40 characters.
MODEL_LABELS = {"intfloat/multilingual-e5-base": "e5", "BAAI/bge-m3": "bge_m3"}


def model_slug(model_id: str) -> str:
    return MODEL_LABELS.get(model_id) or re.sub(r"[^a-z0-9]+", "_", model_id.lower()).strip("_")


def collections_for(model_id: str) -> dict[RetrievalEntityType, str]:
    slug = model_slug(model_id)
    return {
        RetrievalEntityType.PERSON: f"sv2_{slug}_persons",
        RetrievalEntityType.EVENT: f"sv2_{slug}_events",
    }


def store_for(session_factory: sessionmaker[Session], model_id: str) -> PgVectorStore:
    # PERSON exact, EVENT HNSW: the production pgvector configuration (ADR 0018).
    return PgVectorStore(
        session_factory,
        exact_collections=[collections_for(model_id)[RetrievalEntityType.PERSON]],
    )


def load_documents(
    session_factory: sessionmaker[Session], entity_type: RetrievalEntityType
) -> list[SemanticDocument]:
    with session_factory() as session:
        rows = session.scalars(
            select(SemanticDocumentRecord)
            .where(SemanticDocumentRecord.entity_type == entity_type.value)
            .order_by(SemanticDocumentRecord.entity_id)
        ).all()
        return [
            SemanticDocument(
                entity_type=entity_type,
                entity_id=row.entity_id,
                text=row.text,
                representation_version=row.representation_version,
                content_hash=row.content_hash,
                source_updated_at=row.source_updated_at,
            )
            for row in rows
        ]


def replay(database_url: str) -> None:
    manifest = load_manifest(DEFAULT_MANIFEST_PATH)
    cache = RawCorpusCache(DEFAULT_CACHE_DIR)
    golden = load_golden_dataset()
    policy, policy_hash = load_policy()
    inputs = EvaluationInputs(
        manifest=manifest,
        manifest_path=DEFAULT_MANIFEST_PATH,
        cache=cache,
        golden=golden,
        policy=policy,
        policy_hash=policy_hash,
        rf_snapshot=default_rf_snapshot(golden),
        namesakes=None,
    )
    try:
        validate_inputs(inputs, corpus_texts(cache, manifest))
    except EvaluationDataError as exc:
        raise SystemExit(f"evaluation data error: {exc}") from None
    engine = create_database_engine(database_url)
    require_disposable_database(engine)
    started = time.perf_counter()
    runner = CorpusRunner(engine=engine, manifest=manifest, cache=cache)
    # Documents are persisted by the production builders; the vectors of this
    # token-hash in-memory index are thrown away (each model is indexed below).
    runner.create_semantic_indexer = semantic_indexer_factory(
        runner.session_factory, IN_MEMORY_QDRANT, REAL_WORLD_COLLECTIONS
    )
    runner.rebuild_service()
    run_temporal_simulation(runner, inputs.rf_snapshot, rerun=False)
    counts = table_counts(engine)
    engine.dispose()
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    STAND_MANIFEST.write_text(
        json.dumps(
            {
                "dataset_version": golden.version.dataset_version,
                "golden_dataset_hash": golden.content_hash(),
                "corpus_manifest_hash": manifest.content_fingerprint(),
                "manifest_articles": len(manifest.articles),
                "snapshot_id": runner.snapshot_id,
                "person_representation_version": PERSON_REPRESENTATION_VERSION,
                "event_representation_version": EVENT_REPRESENTATION_VERSION,
                "replay_seconds": round(time.perf_counter() - started, 1),
                "table_counts": counts,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        "utf-8",
    )
    print(f"replay done: {counts}")


def index(database_url: str, model_id: str) -> None:
    engine = create_database_engine(database_url)
    require_disposable_database(engine)
    session_factory = create_session_factory(engine)
    embedder = SentenceTransformerEmbedder(EmbeddingConfig(model_id=model_id))
    store = store_for(session_factory, model_id)
    stats: dict[str, object] = {"embedding_model_id": model_id}
    for entity_type, collection in collections_for(model_id).items():
        documents = load_documents(session_factory, entity_type)
        store.recreate_collection(collection, embedder.dimension)
        started = time.perf_counter()
        vectors = embedder.embed_documents([document.text for document in documents])
        embed_seconds = time.perf_counter() - started
        points = [
            VectorPoint(document=document, vector=vector, embedding_model_id=model_id)
            for document, vector in zip(documents, vectors, strict=True)
        ]
        started = time.perf_counter()
        for offset in range(0, len(points), UPSERT_BATCH):
            store.upsert(collection, points[offset : offset + UPSERT_BATCH])
        stats[entity_type.value] = {
            "collection": collection,
            "documents": len(documents),
            "indexed": store.count(collection),
            "embed_seconds": round(embed_seconds, 2),
            "upsert_seconds": round(time.perf_counter() - started, 2),
            "dimension": embedder.dimension,
        }
    engine.dispose()
    path = REPORT_DIR / f"index_{model_slug(model_id)}.json"
    path.write_text(json.dumps(stats, ensure_ascii=False, indent=2) + "\n", "utf-8")
    print(json.dumps(stats, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    replay_parser = sub.add_parser("replay")
    replay_parser.add_argument("--database-url", required=True)
    index_parser = sub.add_parser("index")
    index_parser.add_argument("--database-url", required=True)
    index_parser.add_argument("--model", required=True)
    args = parser.parse_args()
    if args.command == "replay":
        replay(args.database_url)
    else:
        index(args.database_url, args.model)


if __name__ == "__main__":
    main()
