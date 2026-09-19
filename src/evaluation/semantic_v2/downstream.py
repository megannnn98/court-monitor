"""Semantic Retrieval v2: downstream Research Workflow comparison.

The production LangGraph research workflow runs with a deterministic parser
(`PreparedRequestParser`: no language model) on a `semantic_query` request, with
the production retrieval path: hybrid candidates (reranker off), dense similarity
acceptance, ResearchService. Only the embedding model and the threshold change:

    E5@0.80 (production baseline), E5 recalibrated and BGE-M3 calibrated on dev
    (reports/semantic_retrieval_v2/selected_thresholds.json).

Inputs: every PERSON retrieval query of dev and validation (text as semantic_query),
plus the research benchmark's semantic cases (claims judged by ClaimJudge).

    PYTHONPATH=src uv run --group semantic python \
        -m evaluation.semantic_v2.downstream --database-url ...
"""

from __future__ import annotations

import argparse
import json
import math
import time
from collections.abc import Mapping, Sequence
from functools import partial
from statistics import median
from typing import Any

from evaluation.real_world.research_eval import evaluate_research, load_research_queries
from evaluation.real_world.results import Failure
from evaluation.real_world.retrieval_eval import RealRetrievalQuery, load_retrieval_queries
from evaluation.semantic_v2.retrieval import (
    BASELINE_THRESHOLD,
    REPORT_DIR,
    RUN_SPLITS,
    SELECTED_THRESHOLDS_PATH,
    Stand,
    _write_json,
    all_judgments,
    load_pool_judgments,
    load_retired,
    load_stand,
    reachable_keys,
    runnable,
)
from evaluation.semantic_v2.stand import collections_for, store_for
from research.planning.planner import ResearchPlanner
from research.service import ResearchService
from research.unit_of_work import SqlAlchemyResearchUnitOfWork
from research.workflow.graph import build_research_graph, run_research_query
from research.workflow.intake import PreparedRequestParser
from research.workflow.models import WorkflowStatus
from rosfinmonitoring.snapshot_lookup import SqlAlchemyRosfinmonitoringSnapshotLookup
from semantic_retrieval.document_store import PostgresLexicalEntityRetriever
from semantic_retrieval.embeddings import EmbeddingConfig, SentenceTransformerEmbedder
from semantic_retrieval.models import RetrievalEntityType
from semantic_retrieval.relevance import DenseSimilarityRelevancePolicy
from semantic_retrieval.retrievers import HybridEntityRetriever, QdrantEntityRetriever
from sources.source_registry import SOURCES

MODEL_IDS = {"e5": "intfloat/multilingual-e5-base", "bge_m3": "BAAI/bge-m3"}


def configurations() -> dict[str, tuple[str, float]]:
    """name -> (model label, threshold); E5 recalibrated is listed even when unchanged."""
    selected = json.loads(SELECTED_THRESHOLDS_PATH.read_text("utf-8"))
    return {
        f"e5@{BASELINE_THRESHOLD}": ("e5", BASELINE_THRESHOLD),
        f"e5_recalibrated@{selected['e5']}": ("e5", selected["e5"]),
        f"bge_m3_calibrated@{selected['bge_m3']}": ("bge_m3", selected["bge_m3"]),
    }


def graph_extras_for(stand: Stand, model: str, threshold: float) -> dict[str, Any]:
    model_id = MODEL_IDS[model]
    dense = QdrantEntityRetriever(
        embedder=SentenceTransformerEmbedder(EmbeddingConfig(model_id=model_id)),
        store=store_for(stand.session_factory, model_id),
        collections=collections_for(model_id),
    )
    return {
        "candidate_retriever": HybridEntityRetriever(
            lexical=PostgresLexicalEntityRetriever(stand.session_factory), dense=dense
        ),
        "relevance_policy": DenseSimilarityRelevancePolicy(
            dense_min_score=threshold, embedding_model_id=model_id
        ),
    }


def run_queries(
    stand: Stand,
    extras: Mapping[str, Any],
    queries: Sequence[RealRetrievalQuery],
    judgments: Mapping[str, Mapping[str, int]],
) -> dict[str, object]:
    reachable = reachable_keys(stand)[RetrievalEntityType.PERSON]
    person_key = stand.key_of[RetrievalEntityType.PERSON]
    latencies: list[float] = []
    statuses: dict[str, int] = {}
    tp = judged_fp = unjudged = relevant_total = 0
    recalls: list[float] = []
    returned_counts: list[int] = []
    negatives = rejected = negative_returned = 0
    per_query: dict[str, list[str]] = {}
    for query in queries:
        request = {
            "object_type": "person",
            "criteria": {"semantic_query": query.text},
            "limit": 1000,
        }
        graph = build_research_graph(
            request_parser=PreparedRequestParser(request),
            research_service=ResearchService(
                unit_of_work=SqlAlchemyResearchUnitOfWork(stand.session_factory)
            ),
            snapshot_lookup=SqlAlchemyRosfinmonitoringSnapshotLookup(stand.session_factory),
            planner=ResearchPlanner(SOURCES),
            **extras,
        )
        started = time.perf_counter()
        result = run_research_query(graph, query.text)
        latencies.append((time.perf_counter() - started) * 1000)
        statuses[result.status.value] = statuses.get(result.status.value, 0) + 1
        # Report order kept (top-5/top-10); ER split persons share a key, first wins.
        keys = list(
            dict.fromkeys(
                person_key[r.person.id] for r in result.results if r.person.id in person_key
            )
        )
        per_query[query.query_id] = keys
        if result.status is not WorkflowStatus.COMPLETED:
            continue
        if query.expected_no_match:
            negatives += 1
            rejected += not keys
            negative_returned += len(keys)
            continue
        judged = judgments[query.query_id]
        relevant = {k for k, g in judged.items() if g > 0 and k in reachable}
        if not relevant:
            continue
        hits = len(relevant.intersection(keys))
        tp += hits
        relevant_total += len(relevant)
        recalls.append(hits / len(relevant))
        judged_fp += sum(1 for k in keys if judged.get(k) == 0)
        unjudged += sum(1 for k in keys if k not in judged)
        returned_counts.append(len(keys))
    ordered = sorted(latencies)
    return {
        "statuses": statuses,
        "positive_queries": len(recalls),
        "recall_macro": round(sum(recalls) / len(recalls), 4) if recalls else 0.0,
        "recall_micro": round(tp / relevant_total, 4) if relevant_total else 0.0,
        "precision_judged": round(tp / (tp + judged_fp), 4) if tp + judged_fp else 1.0,
        "returned_unjudged": unjudged,
        "returned_median": median(returned_counts) if returned_counts else 0,
        "empty_result_positive_queries": sum(1 for c in returned_counts if c == 0),
        "negative_queries": negatives,
        "negative_rejection_rate": round(rejected / negatives, 4) if negatives else 1.0,
        "negative_persons_returned": negative_returned,
        "latency_ms": {
            "p50": round(median(ordered), 1),
            "p95": round(ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)], 1),
        },
        "returned": per_query,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--database-url", required=True)
    args = parser.parse_args()
    stand = load_stand(args.database_url)
    retired = load_retired()
    queries = [
        q
        for q in runnable(load_retrieval_queries())
        if q.query_id not in retired and q.entity_type is RetrievalEntityType.PERSON
    ]
    judgments = all_judgments(queries, load_pool_judgments())
    research_cases = [
        case
        for case in load_research_queries()
        if case.requires_semantic and case.split in RUN_SPLITS
    ]
    report: dict[str, Any] = {
        "status": "PRELIMINARY (DRAFT judgments)",
        "path": "research graph, PreparedRequestParser, hybrid candidates (reranker off), "
        "dense similarity acceptance, ResearchService",
        "configurations": {},
    }
    for name, (model, threshold) in configurations().items():
        extras = graph_extras_for(stand, model, threshold)
        entry: dict[str, Any] = {"model": MODEL_IDS[model], "threshold": threshold}
        for split in RUN_SPLITS:
            entry[split.value] = run_queries(
                stand, extras, [q for q in queries if q.split is split], judgments
            )
        failures: list[Failure] = []
        section = evaluate_research(
            cases=research_cases,
            dataset=stand.golden,
            state=stand.state,
            identity=stand.identity,
            session_factory=stand.session_factory,
            snapshot_id=json.loads((REPORT_DIR / "stand.json").read_text("utf-8"))["snapshot_id"],
            failures=failures,
            llm_parser=None,
            llm_not_run_reason="deterministic parser only",
            semantic_available=True,
            graph_extras=partial(dict, extras),
        )
        entry["research_benchmark_semantic_cases"] = {
            "cases": [case.query_id for case in research_cases],
            "persons": section.persons,
            "claims": section.claims,
            "contradicted_claims": section.contradicted_claims,
            "dangerous": section.dangerous,
        }
        report["configurations"][name] = entry
        print(
            name,
            {
                s.value: {k: v for k, v in entry[s.value].items() if k != "returned"}
                for s in RUN_SPLITS
            },
        )
    _write_json(REPORT_DIR / "downstream.json", report)


if __name__ == "__main__":
    main()
