"""Experiment: Qdrant vs pgvector on the real semantic corpus, same E5 vectors (ADR 0018).

Three costs are measured apart, so GPU and sentence-transformers noise never hides a
difference between the stores:

1. embedding time — E5 on the documents and queries, once; vectors are cached;
2. vector-store write/index time — full rebuild (recreate + batched upserts, as
   SemanticIndexer does), incremental upserts, and the per-run incremental checks;
3. search time — dense top-100 and top-100 within 200 candidate ids, p50/p95.

Both stores get exactly the same precomputed vectors. Exact nearest neighbours are
computed in numpy, and overlap@100 says how much of them each approximate index finds.

Reads semantic_documents from the working database and never writes to it; vectors go
to a disposable database (name ending _test/_eval) and to Qdrant collections named
bench_*, which are deleted at the end.

    PYTHONPATH=src uv run python evaluation/vector_store/benchmark_vector_stores.py \\
        --source-database-url $WORKING_DB --bench-database-url $TEST_DB \\
        --qdrant-url http://127.0.0.1:6333 --qdrant-container ebnv-qdrant-1
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import subprocess
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any

import numpy as np
from qdrant_client import QdrantClient, models
from sqlalchemy import text

from db.database import create_database_engine, create_session_factory
from db.maintenance import require_disposable_database, truncate_disposable_tables
from semantic_retrieval.embeddings import (
    EmbeddingConfig,
    SentenceTransformerEmbedder,
    parse_device,
)
from semantic_retrieval.models import RetrievalEntityType, SemanticDocument
from semantic_retrieval.pgvector_store import (
    PgVectorStore,
    _vector_literal,
    dense_search_sql,
    ef_search,
    prepare_dense_search,
)
from semantic_retrieval.vector_store import QdrantVectorStore, VectorPoint, VectorStore

CACHE_DIR = Path("var/vector_store_benchmark")
COLLECTIONS = {
    RetrievalEntityType.PERSON: "bench_persons_semantic",
    RetrievalEntityType.EVENT: "bench_events_semantic",
}
BATCH_SIZE = 64  # SemanticIndexer's default
LIMIT = 100  # SEMANTIC_CANDIDATE_POOL_SIZE's default
QUERIES_PER_TYPE = 100
FILTER_SIZE = 200
WARMUP = 10
CHANGED_SHARE = 0.01
SEED = 20260919


@dataclass
class Corpus:
    documents: dict[RetrievalEntityType, list[SemanticDocument]]
    vectors: dict[RetrievalEntityType, np.ndarray]
    queries: dict[RetrievalEntityType, list[str]]
    query_vectors: dict[RetrievalEntityType, np.ndarray]
    embedding: dict[str, Any]


def _load_documents(database_url: str) -> dict[RetrievalEntityType, list[SemanticDocument]]:
    engine = create_database_engine(database_url)
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT entity_type, entity_id, text, representation_version, content_hash "
                "FROM semantic_documents ORDER BY entity_type, entity_id"
            )
        ).all()
    engine.dispose()
    documents: dict[RetrievalEntityType, list[SemanticDocument]] = {t: [] for t in COLLECTIONS}
    for entity_type, entity_id, body, version, content_hash in rows:
        kind = RetrievalEntityType(entity_type)
        documents[kind].append(
            SemanticDocument(
                entity_type=kind,
                entity_id=entity_id,
                text=body,
                representation_version=version,
                content_hash=content_hash,
            )
        )
    return documents


def _queries(documents: Sequence[SemanticDocument], rng: random.Random) -> list[str]:
    """Short queries in the corpus' own words: the first words of random documents."""
    sample = rng.sample(list(documents), QUERIES_PER_TYPE)
    return [" ".join(document.text.split()[:8]) for document in sample]


def _embed(database_url: str, device: str) -> Corpus:
    documents = _load_documents(database_url)
    rng = random.Random(SEED)
    queries = {kind: _queries(documents[kind], rng) for kind in COLLECTIONS}
    embedder = SentenceTransformerEmbedder(EmbeddingConfig(device=parse_device(device)))
    key = embedder.model_id.replace("/", "__")
    cache = CACHE_DIR / f"{key}.npz"
    meta_path = CACHE_DIR / f"{key}.json"
    hashes = {kind: [d.content_hash for d in documents[kind]] for kind in COLLECTIONS}
    if cache.exists() and meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if meta["hashes"] == {kind.value: value for kind, value in hashes.items()}:
            stored = np.load(cache)
            return Corpus(
                documents=documents,
                vectors={kind: stored[f"doc_{kind.value}"] for kind in COLLECTIONS},
                queries=queries,
                query_vectors={kind: stored[f"query_{kind.value}"] for kind in COLLECTIONS},
                embedding=meta["embedding"],
            )
    embedder.embed_query("разогрев")  # model load is not embedding time
    vectors: dict[RetrievalEntityType, np.ndarray] = {}
    query_vectors: dict[RetrievalEntityType, np.ndarray] = {}
    started = time.perf_counter()
    for kind in COLLECTIONS:
        vectors[kind] = np.asarray(
            embedder.embed_documents([d.text for d in documents[kind]]), dtype=np.float32
        )
    document_seconds = time.perf_counter() - started
    query_times: list[float] = []
    for kind in COLLECTIONS:
        rows = []
        for query in queries[kind]:
            begin = time.perf_counter()
            rows.append(embedder.embed_query(query))
            query_times.append(time.perf_counter() - begin)
        query_vectors[kind] = np.asarray(rows, dtype=np.float32)
    total = sum(len(documents[kind]) for kind in COLLECTIONS)
    embedding = {
        "model_id": embedder.model_id,
        "dimension": embedder.dimension,
        "device": device,
        "documents": total,
        "documents_seconds": round(document_seconds, 2),
        "documents_per_second": round(total / document_seconds, 1),
        "query_ms": _percentiles(query_times),
    }
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    arrays: dict[str, Any] = {f"doc_{kind.value}": vectors[kind] for kind in COLLECTIONS}
    arrays |= {f"query_{kind.value}": query_vectors[kind] for kind in COLLECTIONS}
    np.savez(cache, **arrays)
    meta_path.write_text(
        json.dumps(
            {
                "hashes": {kind.value: value for kind, value in hashes.items()},
                "embedding": embedding,
            }
        ),
        encoding="utf-8",
    )
    return Corpus(documents, vectors, queries, query_vectors, embedding)


def _percentiles(seconds: Sequence[float]) -> dict[str, float]:
    ms = sorted(value * 1000 for value in seconds)
    return {
        "n": len(ms),
        "p50": round(statistics.median(ms), 3),
        "p95": round(ms[max(0, round(0.95 * len(ms)) - 1)], 3),
        "mean": round(statistics.fmean(ms), 3),
    }


def _points(
    documents: Sequence[SemanticDocument], vectors: np.ndarray, model_id: str
) -> list[VectorPoint]:
    return [
        VectorPoint(document=document, vector=vector.tolist(), embedding_model_id=model_id)
        for document, vector in zip(documents, vectors, strict=True)
    ]


def _timed(action: Callable[[], object]) -> float:
    started = time.perf_counter()
    action()
    return time.perf_counter() - started


def _exact(
    corpus: Corpus, kind: RetrievalEntityType, query: np.ndarray, ids: np.ndarray | None
) -> list[int]:
    vectors = corpus.vectors[kind]
    entity_ids = np.array([d.entity_id for d in corpus.documents[kind]])
    norms = np.linalg.norm(vectors, axis=1) * np.linalg.norm(query)
    scores = vectors @ query / norms
    if ids is not None:
        mask = np.isin(entity_ids, ids)
        scores = np.where(mask, scores, -np.inf)
    order = np.argsort(-scores, kind="stable")[:LIMIT]
    return [int(entity_ids[i]) for i in order if np.isfinite(scores[i])]


def _run_store(
    name: str,
    store: VectorStore,
    corpus: Corpus,
    *,
    wait_until_indexed: Callable[[str], float],
    size_of: Callable[[str], int],
    inspect: Callable[[str, list[float]], dict[str, Any]],
) -> dict[str, Any]:
    rng = random.Random(SEED)
    return {
        "backend": name,
        "collections": {
            kind.value: _run_collection(
                store, corpus, kind, collection, rng, wait_until_indexed, size_of, inspect
            )
            for kind, collection in COLLECTIONS.items()
        },
    }


def _run_collection(
    store: VectorStore,
    corpus: Corpus,
    kind: RetrievalEntityType,
    collection: str,
    rng: random.Random,
    wait_until_indexed: Callable[[str], float],
    size_of: Callable[[str], int],
    inspect: Callable[[str, list[float]], dict[str, Any]],
) -> dict[str, Any]:
    model = corpus.embedding["model_id"]
    dimension = corpus.embedding["dimension"]
    documents = corpus.documents[kind]
    points = _points(documents, corpus.vectors[kind], model)
    batches = [points[i : i + BATCH_SIZE] for i in range(0, len(points), BATCH_SIZE)]

    # 1. Full rebuild: what SemanticIndexer.rebuild asks of the store.
    recreate = _timed(lambda: store.recreate_collection(collection, dimension))
    batch_times = [_timed(partial(store.upsert, collection, batch)) for batch in batches]
    index_wait = wait_until_indexed(collection)

    # 2. Incremental run: per-run checks, then upserts of the changed documents.
    ensure = _timed(lambda: store.ensure_collection(collection, dimension))
    model_check = _timed(lambda: store.check_embedding_model(collection, model))
    changed_count = max(1, int(len(points) * CHANGED_SHARE))
    changed_idx = rng.sample(range(len(points)), changed_count)
    donors = rng.sample(range(len(points)), changed_count)
    changed = [
        VectorPoint(document=points[i].document, vector=points[j].vector, embedding_model_id=model)
        for i, j in zip(changed_idx, donors, strict=True)
    ]
    changed_times = [
        _timed(partial(store.upsert, collection, changed[i : i + BATCH_SIZE]))
        for i in range(0, len(changed), BATCH_SIZE)
    ]
    # Put the original vectors back, so the searches below compare with numpy.
    for i in range(0, len(changed_idx), BATCH_SIZE):
        store.upsert(collection, [points[k] for k in changed_idx[i : i + BATCH_SIZE]])
    wait_until_indexed(collection)

    # 3. Searches, with the same query vectors for every store. The index state is
    # recorded before and after them: a plan or an unfinished index explains a latency.
    state_before_search = inspect(collection, corpus.query_vectors[kind][0].tolist())
    all_ids = np.array([d.entity_id for d in documents])
    dense_times: list[float] = []
    filtered_times: list[float] = []
    dense_overlap: list[float] = []
    filtered_overlap: list[float] = []
    queries = corpus.query_vectors[kind]
    for warm in queries[:WARMUP]:
        store.search(collection, warm.tolist(), embedding_model_id=model, limit=LIMIT)
    for query in queries:
        vector = query.tolist()
        found: list[int] = []

        def dense(vector: list[float] = vector, found: list[int] = found) -> None:
            found.extend(
                m.entity_id
                for m in store.search(collection, vector, embedding_model_id=model, limit=LIMIT)
            )

        dense_times.append(_timed(dense))
        exact = _exact(corpus, kind, query, None)
        dense_overlap.append(len(set(found) & set(exact)) / len(exact))

        candidate_ids = np.array(rng.sample(list(all_ids), FILTER_SIZE))
        filtered: list[int] = []

        def within(
            vector: list[float] = vector,
            filtered: list[int] = filtered,
            ids: tuple[int, ...] = tuple(int(i) for i in candidate_ids),
        ) -> None:
            filtered.extend(
                m.entity_id
                for m in store.search(
                    collection,
                    vector,
                    embedding_model_id=model,
                    limit=LIMIT,
                    entity_ids=list(ids),
                )
            )

        filtered_times.append(_timed(within))
        exact_filtered = _exact(corpus, kind, query, candidate_ids)
        filtered_overlap.append(len(set(filtered) & set(exact_filtered)) / len(exact_filtered))

    return {
        "documents": len(points),
        "full_rebuild": {
            "recreate_seconds": round(recreate, 3),
            "upsert_seconds": round(sum(batch_times), 2),
            "index_ready_wait_seconds": round(index_wait, 2),
            "points_per_second": round(len(points) / (sum(batch_times) + index_wait), 1),
            "upsert_batch_ms": _percentiles(batch_times),
        },
        "incremental": {
            "ensure_collection_ms": round(ensure * 1000, 2),
            "check_embedding_model_ms": round(model_check * 1000, 2),
            "changed_documents": changed_count,
            "changed_upsert_seconds": round(sum(changed_times), 3),
            "changed_batch_ms": _percentiles(changed_times),
        },
        "dense_search_ms": _percentiles(dense_times),
        "filtered_search_ms": _percentiles(filtered_times),
        "overlap_at_100_vs_exact": {
            "dense_mean": round(statistics.fmean(dense_overlap), 4),
            "dense_min": round(min(dense_overlap), 4),
            "filtered_mean": round(statistics.fmean(filtered_overlap), 4),
            "filtered_min": round(min(filtered_overlap), 4),
        },
        "size_bytes": size_of(collection),
        "state_before_search": state_before_search,
        "state_after_search": inspect(collection, corpus.query_vectors[kind][0].tolist()),
    }


def _pgvector(
    database_url: str,
) -> tuple[
    PgVectorStore,
    Callable[[str], int],
    Callable[[str, list[float]], dict[str, Any]],
    Callable[[], None],
]:
    engine = create_database_engine(database_url)
    require_disposable_database(engine)
    truncate_disposable_tables(engine)
    session_factory = create_session_factory(engine)

    def size_of(collection: str) -> int:
        with engine.connect() as connection:
            indexes = connection.execute(
                text(
                    "SELECT coalesce(sum(pg_relation_size(indexrelid)), 0) FROM pg_index i "
                    "JOIN pg_class c ON c.oid = i.indexrelid "
                    "WHERE c.relname LIKE :pattern"
                ),
                {"pattern": f"ix_semvec_hnsw_{collection}_%"},
            ).scalar_one()
            share = connection.execute(
                text(
                    "SELECT coalesce(sum(pg_column_size(t.*)), 0) FROM semantic_vectors t "
                    "WHERE collection_name = :name"
                ),
                {"name": collection},
            ).scalar_one()
        return int(indexes) + int(share)

    def inspect(collection: str, vector: list[float]) -> dict[str, Any]:
        """The dense query's plan as PgVectorStore runs it, the plan the planner would pick
        without the store's settings, and the table statistics behind both."""
        size = len(vector)
        sql = "EXPLAIN " + dense_search_sql(collection, size)
        parameters = {"query": _vector_literal(vector), "limit": LIMIT}

        def plan(with_store_settings: bool) -> list[str]:
            with session_factory.begin() as session:
                if with_store_settings:
                    prepare_dense_search(session, size, LIMIT)
                else:
                    session.execute(text(f"SET LOCAL hnsw.ef_search = {ef_search(LIMIT)}"))
                return list(session.execute(text(sql), parameters).scalars())

        store_plan, planner_plan = plan(True), plan(False)
        with engine.connect() as connection:
            stats = connection.execute(
                text(
                    "SELECT c.reltuples, s.n_live_tup, s.n_dead_tup, s.last_autoanalyze, "
                    "s.last_analyze FROM pg_class c JOIN pg_stat_user_tables s "
                    "ON s.relid = c.oid WHERE c.relname = 'semantic_vectors'"
                )
            ).one()
        return {
            "store_plan_uses_hnsw": any("ix_semvec_hnsw" in line for line in store_plan),
            "planner_alone_uses_hnsw": any("ix_semvec_hnsw" in line for line in planner_plan),
            "planner_alone_plan_top": planner_plan[:2],
            "reltuples": float(stats[0]),
            "n_live_tup": int(stats[1]),
            "n_dead_tup": int(stats[2]),
            "last_autoanalyze": None if stats[3] is None else stats[3].isoformat(),
            "last_analyze": None if stats[4] is None else stats[4].isoformat(),
        }

    def cleanup() -> None:
        truncate_disposable_tables(engine)
        engine.dispose()

    return PgVectorStore(session_factory), size_of, inspect, cleanup


def _qdrant(
    url: str, container: str | None
) -> tuple[
    QdrantVectorStore,
    Callable[[str], float],
    Callable[[str], int],
    Callable[[str, list[float]], dict[str, Any]],
    Callable[[], None],
]:
    client = QdrantClient(url=url, timeout=60, check_compatibility=False)

    def wait_until_indexed(collection: str) -> float:
        # Qdrant builds its HNSW graph in the background; searches before that are
        # partly brute force, so the index time includes waiting for it.
        started = time.perf_counter()
        while True:
            info = client.get_collection(collection)
            if info.status == models.CollectionStatus.GREEN:
                return time.perf_counter() - started
            time.sleep(0.2)

    def size_of(collection: str) -> int:
        if container is None:
            return -1
        output = subprocess.run(
            ["docker", "exec", container, "du", "-sb", f"/qdrant/storage/collections/{collection}"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        return int(output.split()[0])

    def inspect(collection: str, vector: list[float]) -> dict[str, Any]:
        info = client.get_collection(collection)
        return {
            "status": str(info.status),
            "points_count": info.points_count,
            "indexed_vectors_count": info.indexed_vectors_count,
            "segments_count": info.segments_count,
        }

    def cleanup() -> None:
        for collection in COLLECTIONS.values():
            if client.collection_exists(collection):
                client.delete_collection(collection)

    return QdrantVectorStore(client), wait_until_indexed, size_of, inspect, cleanup


def _markdown(report: dict[str, Any]) -> str:
    embedding = report["embedding"]
    lines = [
        "# Qdrant vs pgvector: same E5 vectors",
        "",
        (
            f"Corpus: `semantic_documents` of the working database, {embedding['documents']} "
            f"documents; model `{embedding['model_id']}` ({embedding['dimension']} d)."
        ),
        (
            f"Embedding (measured once, {embedding['device']}): "
            f"{embedding['documents_seconds']} s for the documents "
            f"({embedding['documents_per_second']} docs/s); query p50 "
            f"{embedding['query_ms']['p50']} ms, p95 {embedding['query_ms']['p95']} ms."
        ),
        "",
        (
            "Store times below exclude embedding. Search: top-100; filtered = top-100 within "
            "200 random candidate ids. Overlap@100 = share of the exact (numpy) top-100 found."
        ),
        "",
        (
            "| collection | backend | full rebuild, s | index ready wait, s | incr. checks, ms "
            "| 1% changed upsert, s | dense p50 / p95, ms | filtered p50 / p95, ms "
            "| overlap dense | overlap filtered | HNSW used | size, MB |"
        ),
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for kind in COLLECTIONS:
        for run in report["stores"]:
            c = run["collections"][kind.value]
            full = c["full_rebuild"]
            incremental = c["incremental"]
            lines.append(
                f"| {kind.value} ({c['documents']}) | {run['backend']} "
                f"| {full['recreate_seconds'] + full['upsert_seconds']:.2f} "
                f"| {full['index_ready_wait_seconds']:.2f} "
                f"| {incremental['ensure_collection_ms'] + incremental['check_embedding_model_ms']:.1f} "
                f"| {incremental['changed_upsert_seconds']:.3f} "
                f"| {c['dense_search_ms']['p50']:.2f} / {c['dense_search_ms']['p95']:.2f} "
                f"| {c['filtered_search_ms']['p50']:.2f} / {c['filtered_search_ms']['p95']:.2f} "
                f"| {c['overlap_at_100_vs_exact']['dense_mean']:.4f} "
                f"| {c['overlap_at_100_vs_exact']['filtered_mean']:.4f} "
                f"| {_hnsw_used(c['state_before_search'])} "
                f"| {c['size_bytes'] / 1e6:.1f} |"
            )
    lines += [
        "",
        "Notes:",
        "",
        (
            "- Qdrant builds HNSW only for segments above `indexing_threshold` (10 000 KB by "
            "default); when `indexed_vectors_count` is 0 its searches are exact."
        ),
        (
            "- Qdrant size is its collection directory right after the load (segments not "
            "yet merged, preallocated files); pgvector size is the rows' bytes plus the HNSW "
            "index."
        ),
        (
            "- pgvector: the store's dense query takes the HNSW index even when the table "
            "statistics lag behind a bulk load (`enable_sort = off` in its transaction)."
        ),
    ]
    return "\n".join(lines) + "\n"


def _hnsw_used(state: dict[str, Any]) -> str:
    if "indexed_vectors_count" in state:
        return (
            f"no ({state['indexed_vectors_count']} indexed)"
            if not state["indexed_vectors_count"]
            else f"{state['indexed_vectors_count']} indexed"
        )
    return "yes" if state.get("store_plan_uses_hnsw") else "no"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-database-url", required=True)
    parser.add_argument("--bench-database-url", required=True)
    parser.add_argument("--qdrant-url", required=True)
    parser.add_argument("--qdrant-container", default=None)
    parser.add_argument("--device", default="auto")
    parser.add_argument("--output", type=Path, default=Path("reports/vector_store_benchmark"))
    args = parser.parse_args()

    corpus = _embed(args.source_database_url, args.device)
    pg_store, pg_size, pg_inspect, pg_cleanup = _pgvector(args.bench_database_url)
    qd_store, qd_wait, qd_size, qd_inspect, qd_cleanup = _qdrant(
        args.qdrant_url, args.qdrant_container
    )
    try:
        stores = [
            _run_store(
                "qdrant",
                qd_store,
                corpus,
                wait_until_indexed=qd_wait,
                size_of=qd_size,
                inspect=qd_inspect,
            ),
            _run_store(
                "pgvector",
                pg_store,
                corpus,
                wait_until_indexed=lambda _: 0.0,
                size_of=pg_size,
                inspect=pg_inspect,
            ),
        ]
    finally:
        qd_cleanup()
        pg_cleanup()
    report = {
        "embedding": corpus.embedding,
        "settings": {
            "batch_size": BATCH_SIZE,
            "limit": LIMIT,
            "queries_per_type": QUERIES_PER_TYPE,
            "filter_size": FILTER_SIZE,
            "changed_share": CHANGED_SHARE,
            "seed": SEED,
            "pgvector_hnsw": (
                "m=16, ef_construction=64 (defaults), ef_search=min(1000, max(40, 4 x limit))"
            ),
            "qdrant_hnsw": (
                "m=16, ef_construct=100, indexing_threshold=10000 KB (defaults): segments "
                "below the threshold are searched exactly; see indexed_vectors_count"
            ),
        },
        "stores": stores,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.with_suffix(".json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    args.output.with_suffix(".md").write_text(_markdown(report), encoding="utf-8")
    print(_markdown(report))


if __name__ == "__main__":
    main()
