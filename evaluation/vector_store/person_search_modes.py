"""Experiment: which pgvector search mode for PERSON, measured against Qdrant.

Modes (EVENT stays on the current HNSW in all of them):

    A   HNSW m=16, ef_construction=64 (current), ef_search 400
    B1  HNSW m=24, ef_construction=200, ef_search 400
    B2  HNSW m=24, ef_construction=200, ef_search 1000
    C   exact: no HNSW index on the person collection at all

The modes live here, in a subclass of PgVectorStore; production code is not changed.
The main measure is not ANN recall but what reaches the research pipeline: the
candidates past the relevance threshold, and the research workflow's final persons.
The workflow runs its production graph; only the LLM request parser is replaced by a
stub that passes the query as `semantic_query`, so results depend on retrieval alone.

Needs a Qdrant copy indexed by validate_real_corpus.py (collections pgv_eval_*) and a
pgvector copy of the same data. Writes only to the pgvector copy.

    PYTHONPATH=src:evaluation/vector_store uv run python \\
        evaluation/vector_store/person_search_modes.py --qdrant-database-url ... \\
        --pgvector-database-url ... --output-dir reports/person_search_modes
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import statistics
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker
from validate_real_corpus import POOL, QUERIES, TimedStore, _components

from research.planning.planner import ResearchPlanner
from research.service import ResearchService
from research.unit_of_work import SqlAlchemyResearchUnitOfWork
from research.workflow.graph import build_research_graph, run_research_query
from research.workflow.models import ResearchIntake
from rosfinmonitoring.snapshot_lookup import SqlAlchemyRosfinmonitoringSnapshotLookup
from semantic_retrieval.embeddings import TextEmbedder
from semantic_retrieval.factory import SemanticComponents
from semantic_retrieval.models import (
    RetrievalBackend,
    RetrievalEntityType,
    RetrievalQuery,
    RetrievalResult,
    RetrievalUnavailableError,
    VectorSizeMismatchError,
)
from semantic_retrieval.pgvector_store import (
    PgVectorStore,
    _index_name,
    _index_predicate,
    _vector_literal,
    dense_search_sql,
)
from semantic_retrieval.reranking import CrossEncoderReranker, RerankerConfig
from semantic_retrieval.vector_store import VectorMatch, require_model
from sources.source_registry import SOURCES

PERSON = RetrievalEntityType.PERSON
BUILDS: dict[str, tuple[int, int] | None] = {"m16": (16, 64), "m24": (24, 200), "exact": None}
MODES: dict[str, tuple[str, int | None]] = {
    "A_hnsw_m16_ef400": ("m16", 400),
    "B1_hnsw_m24_ef400": ("m24", 400),
    "B2_hnsw_m24_ef1000": ("m24", 1000),
    "C_exact": ("exact", None),
}
WARMUP_PASSES = 1
TIMED_PASSES = 5


class ModeStore(PgVectorStore):
    """PgVectorStore whose person collection uses one of the modes; events unchanged."""

    def __init__(
        self, session_factory: sessionmaker[Session], person_collection: str, build: str
    ) -> None:
        super().__init__(session_factory)
        self._person_collection = person_collection
        self._build = build
        self.ef_search = 400

    def _create(self, session: Session, name: str, vector_size: int) -> None:
        if name != self._person_collection:
            super()._create(session, name, vector_size)
            return
        session.execute(
            text(
                "INSERT INTO semantic_vector_collections (name, vector_size) "
                "VALUES (:name, :size) ON CONFLICT (name) DO NOTHING"
            ),
            {"name": name, "size": vector_size},
        )
        params = BUILDS[self._build]
        if params is None:
            return  # exact: no approximate index exists for the planner to take
        m, ef_construction = params
        session.execute(
            text(
                f"CREATE INDEX IF NOT EXISTS {_index_name(name, vector_size)} "
                "ON semantic_vectors USING hnsw "
                f"((embedding::vector({vector_size})) vector_cosine_ops) "
                f"WITH (m = {m}, ef_construction = {ef_construction}) "
                f"WHERE {_index_predicate(name, vector_size)}"
            )
        )

    def person_sql(self, vector_size: int) -> str:
        if self._build == "exact":
            # Uncast distance: no index could serve it, and none exists.
            return (
                "SELECT entity_id, embedding_model_id, "
                "1 - (embedding <=> CAST(:query AS vector)) AS score FROM semantic_vectors "
                f"WHERE {_index_predicate(self._person_collection, vector_size)} "
                "ORDER BY embedding <=> CAST(:query AS vector), entity_id LIMIT :limit"
            )
        return dense_search_sql(self._person_collection, vector_size)

    def prepare(self, session: Session) -> None:
        if self._build != "exact":
            session.execute(text(f"SET LOCAL hnsw.ef_search = {self.ef_search}"))
            session.execute(text("SET LOCAL enable_sort = off"))

    def search(
        self,
        name: str,
        vector: Sequence[float],
        *,
        embedding_model_id: str,
        limit: int,
        entity_ids: Sequence[int] | None = None,
    ) -> list[VectorMatch]:
        if name != self._person_collection or entity_ids is not None:
            return super().search(
                name,
                vector,
                embedding_model_id=embedding_model_id,
                limit=limit,
                entity_ids=entity_ids,
            )

        def run(session: Session) -> list[tuple[int, str, float]]:
            size = self._vector_size(session, name)
            if size is None:
                raise RetrievalUnavailableError(f"Collection {name} does not exist")
            if size != len(vector):
                raise VectorSizeMismatchError(f"Query size {len(vector)} != {size}")
            self.prepare(session)
            rows = session.execute(
                text(self.person_sql(size)), {"query": _vector_literal(vector), "limit": limit}
            ).all()
            return [(int(r[0]), str(r[1]), float(r[2])) for r in rows]

        matches = []
        for entity_id, model, score in self._call("search", run):
            require_model(name, model, embedding_model_id)
            matches.append(VectorMatch(entity_id=entity_id, score=score))
        return matches


class CachingEmbedder:
    """Every mode rebuilds from the same texts: embed each text once, time it once."""

    def __init__(self, inner: TextEmbedder) -> None:
        self._inner = inner
        self._documents: dict[str, list[float]] = {}
        self._queries: dict[str, list[float]] = {}
        self.seconds = 0.0

    @property
    def model_id(self) -> str:
        return self._inner.model_id

    @property
    def dimension(self) -> int:
        return self._inner.dimension

    def embed_query(self, text: str) -> list[float]:
        if text not in self._queries:
            self._queries[text] = self._inner.embed_query(text)
        return self._queries[text]

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        missing = [t for t in dict.fromkeys(texts) if t not in self._documents]
        if missing:
            started = time.perf_counter()
            for t, v in zip(missing, self._inner.embed_documents(missing), strict=True):
                self._documents[t] = v
            self.seconds += time.perf_counter() - started
        return [self._documents[t] for t in texts]


class SemanticOnlyParser:
    """Stands in for the LLM intake: the query is the semantic criterion, nothing more."""

    def parse(self, raw: str) -> ResearchIntake:
        return ResearchIntake(
            request={"object_type": "person", "criteria": {"semantic_query": raw}}
        )


def _ids(result: RetrievalResult) -> list[int]:
    return [hit.entity_id for hit in result.hits]


def _percentiles(ms: list[float]) -> dict[str, float]:
    ordered = sorted(ms)
    return {
        "n": len(ordered),
        "p50": round(statistics.median(ordered), 2),
        "p95": round(ordered[max(0, round(0.95 * len(ordered)) - 1)], 2),
        "max": round(ordered[-1], 2),
    }


def _overlap(a: Sequence[int], b: Sequence[int], k: int) -> float:
    top_a, top_b = set(a[:k]), set(b[:k])
    return len(top_a & top_b) / max(1, min(k, max(len(top_a), len(top_b))))


def _jaccard(a: Sequence[int], b: Sequence[int]) -> float:
    union = set(a) | set(b)
    return 1.0 if not union else len(set(a) & set(b)) / len(union)


def _workflow(components: SemanticComponents, retriever_backend: RetrievalBackend) -> Any:
    session_factory = components.session_factory
    return build_research_graph(
        request_parser=SemanticOnlyParser(),
        research_service=ResearchService(
            unit_of_work=SqlAlchemyResearchUnitOfWork(session_factory)
        ),
        snapshot_lookup=SqlAlchemyRosfinmonitoringSnapshotLookup(session_factory),
        planner=ResearchPlanner(SOURCES, candidate_pool_size=POOL),
        candidate_retriever=components.retriever(retriever_backend),
        relevance_policy=components.relevance_policy(),
    )


def _pipeline(components: SemanticComponents, queries: list[dict[str, Any]]) -> dict[str, Any]:
    """What each person query yields at every stage of the research pipeline."""
    policy = components.relevance_policy()
    graph = _workflow(components, RetrievalBackend.HYBRID)
    out: dict[str, Any] = {}
    for query in queries:
        request = RetrievalQuery(text=query["text"], entity_type=PERSON, limit=POOL)
        hybrid = components.retriever(RetrievalBackend.HYBRID).retrieve(request)
        reranked = components.retriever(RetrievalBackend.HYBRID_RERANKED).retrieve(request)
        result = run_research_query(graph, query["text"])
        out[query["id"]] = {
            "dense": _ids(components.retriever(RetrievalBackend.DENSE).retrieve(request)),
            "accepted": _ids(policy.accept(request, hybrid).accepted),
            "reranked": _ids(reranked),
            "workflow_status": result.status.value,
            "workflow_persons": [r.person.id for r in result.results],
        }
    return out


def _latency(
    store: ModeStore, components: SemanticComponents, vectors: list[list[float]]
) -> dict[str, float]:
    collection = components.collections[PERSON]
    model = components.embedder.model_id
    for _ in range(WARMUP_PASSES):
        for vector in vectors:
            store.search(collection, vector, embedding_model_id=model, limit=POOL)
    samples: list[float] = []
    for _ in range(TIMED_PASSES):
        for vector in vectors:
            started = time.perf_counter()
            store.search(collection, vector, embedding_model_id=model, limit=POOL)
            samples.append((time.perf_counter() - started) * 1000)
    return _percentiles(samples)


def _plans(
    store: ModeStore, components: SemanticComponents, vectors: list[list[float]]
) -> dict[str, Any]:
    size = components.embedder.dimension
    plans = []
    for vector in vectors:
        with components.session_factory.begin() as session:
            store.prepare(session)
            lines = list(
                session.execute(
                    text("EXPLAIN (ANALYZE, BUFFERS) " + store.person_sql(size)),
                    {"query": _vector_literal(vector), "limit": POOL},
                ).scalars()
            )
        plans.append(
            {
                "uses_hnsw": any("hnsw" in line.lower() for line in lines),
                "lines": [line.strip()[:160] for line in lines if "::vector" not in line][:8],
            }
        )
    with components.session_factory() as session:
        indexes = list(
            session.execute(
                text(
                    "SELECT indexname FROM pg_indexes WHERE tablename = 'semantic_vectors' "
                    "ORDER BY indexname"
                )
            ).scalars()
        )
    return {
        "indexes_on_semantic_vectors": indexes,
        "queries_using_hnsw": sum(p["uses_hnsw"] for p in plans),
        "queries": len(plans),
        "first_plan": plans[0]["lines"],
        "plans": plans,
    }


def _sizes(components: SemanticComponents) -> dict[str, int]:
    with components.session_factory() as session:
        rows = session.execute(
            text(
                "SELECT c.relname, pg_relation_size(c.oid) FROM pg_index i "
                "JOIN pg_class c ON c.oid = i.indexrelid "
                "WHERE c.relname LIKE 'ix_semvec_hnsw_%'"
            )
        ).all()
        person_rows = session.execute(
            text(
                "SELECT coalesce(sum(pg_column_size(t.*)), 0) FROM semantic_vectors t "
                "WHERE collection_name = :c"
            ),
            {"c": components.collections[PERSON]},
        ).scalar_one()
    return {str(name): int(size) for name, size in rows} | {"person_rows": int(person_rows)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qdrant-database-url", required=True)
    parser.add_argument("--pgvector-database-url", required=True)
    parser.add_argument("--qdrant-url", default="http://127.0.0.1:6333")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    queries = [
        q
        for q in json.loads(QUERIES.read_text(encoding="utf-8"))["queries"]
        if q["entity_type"] == "person"
    ]

    qdrant = _with_reranker(_components(args.qdrant_database_url, "qdrant", args.qdrant_url))
    base = _with_reranker(_components(args.pgvector_database_url, "pgvector", args.qdrant_url))
    embedder = CachingEmbedder(base.embedder)
    reference = _pipeline(qdrant, queries)
    (args.output_dir / "qdrant_pipeline.json").write_text(
        json.dumps(reference, indent=1) + "\n", encoding="utf-8"
    )

    report: dict[str, Any] = {"queries": len(queries), "builds": {}, "modes": {}}
    for build in BUILDS:
        store = ModeStore(base.session_factory, base.collections[PERSON], build)
        timed = TimedStore(store)
        components = dataclasses.replace(base, store=timed, embedder=embedder)
        indexer = components.indexer()
        embedded_before = embedder.seconds
        started = time.perf_counter()
        for entity_type in (PERSON, RetrievalEntityType.EVENT):
            indexer.rebuild(entity_type)
        total = time.perf_counter() - started
        with components.session_factory.begin() as session:
            session.execute(text("ANALYZE semantic_vectors"))
        report["builds"][build] = {
            "rebuild_seconds_total": round(total, 1),
            "embedding_seconds_this_run": round(embedder.seconds - embedded_before, 1),
            "vector_store_seconds": {k: round(v, 1) for k, v in timed.seconds.items()},
            "sizes_bytes": _sizes(components),
        }
        exact_ids = {
            q["id"]: _exact_ids(components, embedder.embed_query(q["text"])) for q in queries
        }
        vectors = [embedder.embed_query(q["text"]) for q in queries]
        for mode, (mode_build, ef) in MODES.items():
            if mode_build != build:
                continue
            if ef is not None:
                store.ef_search = ef
            mode_components = dataclasses.replace(base, store=store, embedder=embedder)
            pipeline = _pipeline(mode_components, queries)
            report["modes"][mode] = _summarize(pipeline, reference, exact_ids)
            report["modes"][mode]["latency_ms"] = _latency(store, mode_components, vectors)
            plans = _plans(store, mode_components, vectors)
            report["modes"][mode]["plans"] = {k: v for k, v in plans.items() if k != "plans"}
            (args.output_dir / f"{mode}_pipeline.json").write_text(
                json.dumps(pipeline, indent=1) + "\n", encoding="utf-8"
            )
            (args.output_dir / f"{mode}_explain.json").write_text(
                json.dumps(plans, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
            )
            print(
                mode, json.dumps({k: v for k, v in report["modes"][mode].items() if k != "plans"})
            )
    report["embedding_seconds_once"] = round(embedder.seconds, 1)
    (args.output_dir / "summary.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def _with_reranker(components: SemanticComponents) -> SemanticComponents:
    """The cross-encoder of SEMANTIC_RERANK=1, for the reranked order."""
    return dataclasses.replace(components, reranker=CrossEncoderReranker(RerankerConfig.from_env()))


def _exact_ids(components: SemanticComponents, vector: list[float]) -> list[int]:
    with components.session_factory() as session:
        return list(
            session.execute(
                text(
                    "SELECT entity_id FROM semantic_vectors WHERE collection_name = :c "
                    "ORDER BY embedding <=> CAST(:q AS vector), entity_id LIMIT :limit"
                ),
                {"c": components.collections[PERSON], "q": _vector_literal(vector), "limit": POOL},
            ).scalars()
        )


def _summarize(
    pipeline: dict[str, Any], reference: dict[str, Any], exact: dict[str, list[int]]
) -> dict[str, Any]:
    rows = []
    for qid, mine in pipeline.items():
        ref = reference[qid]
        rows.append(
            {
                "overlap@10": _overlap(mine["dense"], ref["dense"], 10),
                "overlap@100": _overlap(mine["dense"], ref["dense"], 100),
                "exact_recall@100": _overlap(mine["dense"], exact[qid], 100),
                "accepted": len(mine["accepted"]),
                "accepted_same_set": set(mine["accepted"]) == set(ref["accepted"]),
                "accepted_same_order": mine["accepted"] == ref["accepted"],
                "accepted_jaccard": _jaccard(mine["accepted"], ref["accepted"]),
                "reranked_same_order": mine["reranked"] == ref["reranked"],
                "reranked_top10_same_order": mine["reranked"][:10] == ref["reranked"][:10],
                "reranked_overlap@10": _overlap(mine["reranked"], ref["reranked"], 10),
                "workflow_same_status": mine["workflow_status"] == ref["workflow_status"],
                "workflow_same_set": set(mine["workflow_persons"]) == set(ref["workflow_persons"]),
                "workflow_same_order": mine["workflow_persons"] == ref["workflow_persons"],
                "workflow_same_top5": mine["workflow_persons"][:5] == ref["workflow_persons"][:5],
                "workflow_same_top10": mine["workflow_persons"][:10]
                == ref["workflow_persons"][:10],
                "workflow_jaccard": _jaccard(mine["workflow_persons"], ref["workflow_persons"]),
                "workflow_persons": len(mine["workflow_persons"]),
            }
        )
    n = len(rows)

    def mean(key: str) -> float:
        return round(statistics.fmean(float(r[key]) for r in rows), 4)

    def count(key: str) -> str:
        return f"{sum(bool(r[key]) for r in rows)}/{n}"

    return {
        "dense": {
            "overlap@10_mean": mean("overlap@10"),
            "overlap@10_min": min(r["overlap@10"] for r in rows),
            "overlap@100_mean": mean("overlap@100"),
            "overlap@100_min": min(r["overlap@100"] for r in rows),
            "exact_recall@100_mean": mean("exact_recall@100"),
            "exact_recall@100_min": min(r["exact_recall@100"] for r in rows),
        },
        "threshold": {
            "accepted_mean": mean("accepted"),
            "same_set_as_qdrant": count("accepted_same_set"),
            "same_order_as_qdrant": count("accepted_same_order"),
            "jaccard_mean": mean("accepted_jaccard"),
            "jaccard_min": round(min(r["accepted_jaccard"] for r in rows), 4),
        },
        "reranked": {
            "same_full_order": count("reranked_same_order"),
            "same_top10_order": count("reranked_top10_same_order"),
            "overlap@10_mean": mean("reranked_overlap@10"),
        },
        "workflow": {
            "same_status": count("workflow_same_status"),
            "same_person_set": count("workflow_same_set"),
            "same_full_order": count("workflow_same_order"),
            "same_top5": count("workflow_same_top5"),
            "same_top10": count("workflow_same_top10"),
            "jaccard_mean": mean("workflow_jaccard"),
            "jaccard_min": round(min(r["workflow_jaccard"] for r in rows), 4),
            "persons_mean": mean("workflow_persons"),
        },
        "per_query": dict(zip(pipeline, rows, strict=True)),
    }


if __name__ == "__main__":
    main()
