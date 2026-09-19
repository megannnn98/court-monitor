"""Experiment: pgvector against Qdrant on copies of the working database (ADR 0018).

Every run goes through the production components (`create_semantic_components`,
`SemanticIndexer`, the dense and hybrid retrievers, the relevance policy); only the
embedder and the vector store are wrapped to time them apart. Each backend has its own
copy of the database, restored from one dump, because `semantic_index_state` makes the
two backends' incremental runs on one database different tests.

    rebuild  — full or incremental index build of one copy, with times and stats
    compare  — the same queries on both copies: top-k parity, ranks, scores, and the
               exact top-k to explain every difference
    plans    — EXPLAIN ANALYZE of pgvector's searches, with and without its settings

The queries check parity of the two stores, not relevance. Nothing here writes to the
working database: the copies are named on the command line.

    PYTHONPATH=src uv run python evaluation/vector_store/validate_real_corpus.py rebuild \\
        --database-url $COPY --backend pgvector --output reports/...json
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import statistics
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from sqlalchemy import text

from db.database import create_database_engine, create_session_factory
from semantic_retrieval.embeddings import TextEmbedder
from semantic_retrieval.factory import (
    SemanticComponents,
    SemanticRetrievalConfig,
    VectorBackend,
    create_semantic_components,
)
from semantic_retrieval.models import (
    RetrievalBackend,
    RetrievalEntityType,
    RetrievalQuery,
    RetrievalResult,
)
from semantic_retrieval.pgvector_store import (
    _vector_literal,
    dense_search_sql,
    prepare_dense_search,
)
from semantic_retrieval.vector_store import VectorMatch, VectorPoint, VectorStore

QUERIES = Path("evaluation/vector_store/real_queries.json")
QDRANT_COLLECTIONS = ("pgv_eval_persons_semantic", "pgv_eval_events_semantic")
POOL = 100  # SEMANTIC_CANDIDATE_POOL_SIZE's default


class TimedEmbedder:
    def __init__(self, inner: TextEmbedder) -> None:
        self._inner = inner
        self.document_seconds = 0.0
        self.documents = 0

    @property
    def model_id(self) -> str:
        return self._inner.model_id

    @property
    def dimension(self) -> int:
        return self._inner.dimension

    def embed_query(self, text: str) -> list[float]:
        return self._inner.embed_query(text)

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        started = time.perf_counter()
        vectors = self._inner.embed_documents(texts)
        self.document_seconds += time.perf_counter() - started
        self.documents += len(texts)
        return vectors


class TimedStore:
    def __init__(self, inner: VectorStore) -> None:
        self._inner = inner
        self.seconds: dict[str, float] = {}

    @contextmanager
    def _timed(self, name: str) -> Iterator[None]:
        started = time.perf_counter()
        try:
            yield
        finally:
            self.seconds[name] = self.seconds.get(name, 0.0) + time.perf_counter() - started

    @property
    def backend_name(self) -> str:
        return self._inner.backend_name

    def ensure_collection(self, name: str, vector_size: int) -> None:
        with self._timed("ensure_collection"):
            self._inner.ensure_collection(name, vector_size)

    def recreate_collection(self, name: str, vector_size: int) -> None:
        with self._timed("recreate_collection"):
            self._inner.recreate_collection(name, vector_size)

    def upsert(self, name: str, points: Sequence[VectorPoint]) -> None:
        with self._timed("upsert"):
            self._inner.upsert(name, points)

    def delete(
        self, name: str, entity_type: RetrievalEntityType, entity_ids: Sequence[int]
    ) -> None:
        with self._timed("delete"):
            self._inner.delete(name, entity_type, entity_ids)

    def search(
        self,
        name: str,
        vector: Sequence[float],
        *,
        embedding_model_id: str,
        limit: int,
        entity_ids: Sequence[int] | None = None,
    ) -> list[VectorMatch]:
        return self._inner.search(
            name, vector, embedding_model_id=embedding_model_id, limit=limit, entity_ids=entity_ids
        )

    def check_embedding_model(self, name: str, embedding_model_id: str) -> None:
        with self._timed("check_embedding_model"):
            self._inner.check_embedding_model(name, embedding_model_id)

    def count(self, name: str) -> int:
        return self._inner.count(name)


def _components(database_url: str, backend: VectorBackend, qdrant_url: str) -> SemanticComponents:
    """The production composition; the Qdrant copy gets collections of its own."""
    engine = create_database_engine(database_url)
    config = (
        SemanticRetrievalConfig(
            qdrant_url=qdrant_url,
            person_collection=QDRANT_COLLECTIONS[0],
            event_collection=QDRANT_COLLECTIONS[1],
        )
        if backend == "qdrant"
        else SemanticRetrievalConfig(vector_backend="pgvector")
    )
    return create_semantic_components(create_session_factory(engine), config, with_reranker=False)


def rebuild(args: argparse.Namespace) -> dict[str, Any]:
    components = _components(args.database_url, args.backend, args.qdrant_url)
    embedder = TimedEmbedder(components.embedder)
    store = TimedStore(components.store)
    indexer = dataclasses.replace(components, embedder=embedder, store=store).indexer()
    started = time.perf_counter()
    stats = [
        dataclasses.asdict(indexer.rebuild(entity_type, incremental=args.incremental))
        for entity_type in RetrievalEntityType
    ]
    total = time.perf_counter() - started
    store_seconds = sum(store.seconds.values())
    return {
        "backend": args.backend,
        "incremental": args.incremental,
        "stats": stats,
        "counts": {
            entity_type.value: store.count(components.collections[entity_type])
            for entity_type in RetrievalEntityType
        },
        "seconds": {
            "total": round(total, 2),
            "embedding": round(embedder.document_seconds, 2),
            "vector_store": round(store_seconds, 2),
            "vector_store_by_call": {k: round(v, 3) for k, v in store.seconds.items()},
            "postgresql_documents_and_rest": round(
                total - embedder.document_seconds - store_seconds, 2
            ),
        },
        "documents_embedded": embedder.documents,
    }


def _hits(result: RetrievalResult) -> list[int]:
    return [hit.entity_id for hit in result.hits]


def _dense_scores(result: RetrievalResult) -> dict[int, float]:
    scores: dict[int, float] = {}
    for hit in result.hits:
        score = (
            hit.score
            if hit.backend is RetrievalBackend.DENSE
            else hit.component_scores.get(RetrievalBackend.DENSE.value)
        )
        if score is not None:
            scores[hit.entity_id] = score
    return scores


def _exact(
    pg: SemanticComponents, entity_type: RetrievalEntityType, vector: list[float]
) -> list[int]:
    """The exact nearest neighbours in pgvector (no index): the ground truth for both."""
    collection = pg.collections[entity_type]
    with pg.session_factory() as session:
        return list(
            session.execute(
                text(
                    "SELECT entity_id FROM semantic_vectors WHERE collection_name = :c "
                    "ORDER BY embedding <=> CAST(:q AS vector), entity_id LIMIT :limit"
                ),
                {"c": collection, "q": _vector_literal(vector), "limit": POOL},
            ).scalars()
        )


def _overlap(a: Sequence[int], b: Sequence[int], k: int) -> float:
    top_a, top_b = set(a[:k]), set(b[:k])
    return len(top_a & top_b) / max(1, min(k, max(len(top_a), len(top_b))))


def compare(args: argparse.Namespace) -> dict[str, Any]:
    qd = _components(args.qdrant_database_url, "qdrant", args.qdrant_url)
    pg = _components(args.pgvector_database_url, "pgvector", args.qdrant_url)
    queries = json.loads(QUERIES.read_text(encoding="utf-8"))["queries"]
    policy = pg.relevance_policy()
    rows: list[dict[str, Any]] = []
    for query in queries:
        entity_type = RetrievalEntityType(query["entity_type"])
        request = RetrievalQuery(text=query["text"], entity_type=entity_type, limit=POOL)
        vector = pg.embedder.embed_query(query["text"])
        exact = _exact(pg, entity_type, vector)
        row: dict[str, Any] = {
            "id": query["id"],
            "text": query["text"],
            "entity_type": entity_type.value,
        }
        for backend in (RetrievalBackend.DENSE, RetrievalBackend.HYBRID):
            q_result = qd.retriever(backend).retrieve(request)
            p_result = pg.retriever(backend).retrieve(request)
            q_ids, p_ids = _hits(q_result), _hits(p_result)
            q_scores, p_scores = _dense_scores(q_result), _dense_scores(p_result)
            common = set(q_scores) & set(p_scores)
            entry: dict[str, Any] = {
                "top10_equal_order": q_ids[:10] == p_ids[:10],
                "overlap@10": _overlap(q_ids, p_ids, 10),
                "overlap@100": _overlap(q_ids, p_ids, 100),
                "max_score_diff": max(
                    (abs(q_scores[i] - p_scores[i]) for i in common), default=0.0
                ),
                "qdrant_vs_exact@100": _overlap(q_ids, exact, 100)
                if backend is RetrievalBackend.DENSE
                else None,
                "pgvector_vs_exact@100": _overlap(p_ids, exact, 100)
                if backend is RetrievalBackend.DENSE
                else None,
            }
            if q_ids[:10] != p_ids[:10]:
                entry["top10_differences"] = [
                    {
                        "entity_id": entity_id,
                        "qdrant_rank": q_ids.index(entity_id) + 1 if entity_id in q_ids else None,
                        "pgvector_rank": p_ids.index(entity_id) + 1 if entity_id in p_ids else None,
                        "exact_rank": exact.index(entity_id) + 1 if entity_id in exact else None,
                        "qdrant_dense": q_scores.get(entity_id),
                        "pgvector_dense": p_scores.get(entity_id),
                    }
                    for entity_id in sorted(set(q_ids[:10]) ^ set(p_ids[:10]))
                    or [i for i, j in zip(q_ids[:10], p_ids[:10], strict=False) if i != j]
                ]
            if backend is RetrievalBackend.HYBRID:
                q_accepted = _hits(policy.accept(request, q_result).accepted)
                p_accepted = _hits(policy.accept(request, p_result).accepted)
                entry["accepted_equal"] = q_accepted == p_accepted
                entry["accepted_counts"] = [len(q_accepted), len(p_accepted)]
                if q_accepted != p_accepted:
                    entry["accepted_only_qdrant"] = sorted(set(q_accepted) - set(p_accepted))
                    entry["accepted_only_pgvector"] = sorted(set(p_accepted) - set(q_accepted))
            row[backend.value] = entry
        rows.append(row)

    def summary(backend: str) -> dict[str, Any]:
        entries = [row[backend] for row in rows]
        result = {
            "queries": len(entries),
            "top10_identical_order": sum(e["top10_equal_order"] for e in entries),
            "overlap@10_mean": round(statistics.fmean(e["overlap@10"] for e in entries), 4),
            "overlap@10_min": min(e["overlap@10"] for e in entries),
            "overlap@100_mean": round(statistics.fmean(e["overlap@100"] for e in entries), 4),
            "overlap@100_min": min(e["overlap@100"] for e in entries),
            "max_score_diff": max(e["max_score_diff"] for e in entries),
        }
        if backend == "dense":
            result["qdrant_vs_exact@100_mean"] = round(
                statistics.fmean(e["qdrant_vs_exact@100"] for e in entries), 4
            )
            result["pgvector_vs_exact@100_mean"] = round(
                statistics.fmean(e["pgvector_vs_exact@100"] for e in entries), 4
            )
            result["pgvector_vs_exact@100_min"] = min(e["pgvector_vs_exact@100"] for e in entries)
        else:
            result["accepted_identical"] = sum(e["accepted_equal"] for e in entries)
        return result

    return {"summary": {b: summary(b) for b in ("dense", "hybrid")}, "queries": rows}


def plans(args: argparse.Namespace) -> dict[str, Any]:
    """EXPLAIN ANALYZE of the searches as PgVectorStore runs them, and without its
    settings, over the first queries of the set."""
    pg = _components(args.database_url, "pgvector", args.qdrant_url)
    queries = json.loads(QUERIES.read_text(encoding="utf-8"))["queries"][: args.queries]
    out: list[dict[str, Any]] = []
    with pg.session_factory() as session:
        stats = session.execute(
            text(
                "SELECT c.reltuples, s.n_live_tup, s.last_autoanalyze, s.last_analyze "
                "FROM pg_class c JOIN pg_stat_user_tables s ON s.relid = c.oid "
                "WHERE c.relname = 'semantic_vectors'"
            )
        ).one()
    for query in queries:
        entity_type = RetrievalEntityType(query["entity_type"])
        collection = pg.collections[entity_type]
        vector = _vector_literal(pg.embedder.embed_query(query["text"]))
        size = pg.embedder.dimension
        entry: dict[str, Any] = {"id": query["id"]}
        for label, settings in (("store_settings", True), ("planner_alone", False)):
            with pg.session_factory.begin() as session:
                if settings:
                    prepare_dense_search(session, size, POOL)
                else:
                    session.execute(text("SET LOCAL hnsw.ef_search = 400"))
                lines = list(
                    session.execute(
                        text("EXPLAIN (ANALYZE, BUFFERS) " + dense_search_sql(collection, size)),
                        {"query": vector, "limit": POOL},
                    ).scalars()
                )
            entry[label] = {
                "uses_hnsw": any("ix_semvec_hnsw" in line for line in lines),
                "execution_ms": next(
                    (float(line.split()[2]) for line in lines if line.startswith("Execution Time")),
                    None,
                ),
                # EXPLAIN repeats the query vector: the plan's first lines, cut short.
                "top": [line.strip()[:200] for line in lines[:3]],
            }
        with pg.session_factory() as session:
            ids = list(
                session.execute(
                    text(
                        "SELECT entity_id FROM semantic_vectors WHERE collection_name = :c "
                        "ORDER BY entity_id LIMIT 200"
                    ),
                    {"c": collection},
                ).scalars()
            )
            filtered = list(
                session.execute(
                    text(
                        "EXPLAIN (ANALYZE) SELECT entity_id FROM semantic_vectors "
                        "WHERE collection_name = :c AND entity_id = ANY(:ids) "
                        "ORDER BY embedding <=> CAST(:q AS vector), entity_id LIMIT :limit"
                    ),
                    {"c": collection, "ids": ids, "q": vector, "limit": POOL},
                ).scalars()
            )
        entry["filtered"] = {
            "execution_ms": next(
                (float(line.split()[2]) for line in filtered if line.startswith("Execution Time")),
                None,
            ),
            "top": [line.strip()[:200] for line in filtered[:4]],
        }
        out.append(entry)
    return {
        "statistics": {
            "reltuples": float(stats[0]),
            "n_live_tup": int(stats[1]),
            "last_autoanalyze": None if stats[2] is None else stats[2].isoformat(),
            "last_analyze": None if stats[3] is None else stats[3].isoformat(),
        },
        "queries": out,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qdrant-url", default="http://127.0.0.1:6333")
    parser.add_argument("--output", type=Path, required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("rebuild")
    build.add_argument("--database-url", required=True)
    build.add_argument("--backend", choices=["qdrant", "pgvector"], required=True)
    build.add_argument("--incremental", action="store_true")
    both = commands.add_parser("compare")
    both.add_argument("--qdrant-database-url", required=True)
    both.add_argument("--pgvector-database-url", required=True)
    explain = commands.add_parser("plans")
    explain.add_argument("--database-url", required=True)
    explain.add_argument("--queries", type=int, default=10)
    args = parser.parse_args()
    action = {"rebuild": rebuild, "compare": compare, "plans": plans}[args.command]
    result = action(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8"
    )
    print(json.dumps(result.get("summary", result.get("seconds", {})), ensure_ascii=False))


if __name__ == "__main__":
    main()
