"""Check of the production PERSON exact search on a rebuilt pgvector copy (ADR 0018).

Uses the store the factory builds for SEMANTIC_VECTOR_BACKEND=pgvector: persons exact,
events HNSW. One warm-up pass and five timed passes over the 45 person queries,
EXPLAIN (ANALYZE, BUFFERS) of the first one, and the dense top-100 of every query
compared with the experiment's exact mode and with Qdrant
(reports/person_search_modes/{C_exact,qdrant}_pipeline.json).
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path
from typing import Any

from sqlalchemy import text
from validate_real_corpus import POOL, QUERIES

from db.database import create_database_engine, create_session_factory
from semantic_retrieval.factory import SemanticRetrievalConfig, create_semantic_components
from semantic_retrieval.models import RetrievalBackend, RetrievalEntityType, RetrievalQuery
from semantic_retrieval.pgvector_store import PgVectorStore, _vector_literal

PERSON = RetrievalEntityType.PERSON
MODES = Path("reports/person_search_modes")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    components = create_semantic_components(
        create_session_factory(create_database_engine(args.database_url)),
        SemanticRetrievalConfig(vector_backend="pgvector"),
        with_reranker=False,
    )
    store = components.store
    assert isinstance(store, PgVectorStore)
    collection = components.collections[PERSON]
    model = components.embedder.model_id
    queries = [
        q
        for q in json.loads(QUERIES.read_text(encoding="utf-8"))["queries"]
        if q["entity_type"] == "person"
    ]
    vectors = [components.embedder.embed_query(q["text"]) for q in queries]
    for vector in vectors:
        store.search(collection, vector, embedding_model_id=model, limit=POOL)
    samples: list[float] = []
    for _ in range(5):
        for vector in vectors:
            started = time.perf_counter()
            store.search(collection, vector, embedding_model_id=model, limit=POOL)
            samples.append((time.perf_counter() - started) * 1000)
    samples.sort()
    with components.session_factory() as session:
        plan = [
            line.strip()[:160]
            for line in session.execute(
                text("EXPLAIN (ANALYZE, BUFFERS) " + store.exact_search_sql(collection)),
                {"query": _vector_literal(vectors[0]), "limit": POOL},
            ).scalars()
            if "::vector" not in line
        ]
    dense = components.retriever(RetrievalBackend.DENSE)
    mine = {
        q["id"]: dense.retrieve(
            RetrievalQuery(text=q["text"], entity_type=PERSON, limit=POOL)
        ).entity_ids
        for q in queries
    }
    experiment = json.loads((MODES / "C_exact_pipeline.json").read_text(encoding="utf-8"))
    qdrant = json.loads((MODES / "qdrant_pipeline.json").read_text(encoding="utf-8"))
    report: dict[str, Any] = {
        "latency_ms": {
            "n": len(samples),
            "p50": round(statistics.median(samples), 2),
            "p95": round(samples[round(0.95 * len(samples)) - 1], 2),
            "max": round(samples[-1], 2),
        },
        "plan_uses_hnsw": any("hnsw" in line.lower() for line in plan),
        "plan": plan,
        "top100_equal_to_experiment_exact": sum(mine[k] == experiment[k]["dense"] for k in mine),
        "top100_equal_to_qdrant": sum(mine[k] == qdrant[k]["dense"] for k in mine),
        "top100_same_set_as_qdrant": sum(set(mine[k]) == set(qdrant[k]["dense"]) for k in mine),
        "queries": len(mine),
    }
    args.output.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps({k: v for k, v in report.items() if k != "plan"}, indent=1))
    print("\n".join(plan))


if __name__ == "__main__":
    main()
