"""Developer CLI for semantic retrieval: index, search, evaluate.

Search output is diagnostic: retrieval scores are backend-specific ranking
signals, never confidences of facts. Facts come from `research`/`ask`.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel
from sqlalchemy.orm import Session, sessionmaker

from db.database import create_database_engine, create_session_factory
from db.maintenance import require_disposable_database, truncate_disposable_tables
from semantic_retrieval.document_store import SqlAlchemySemanticDocumentRepository
from semantic_retrieval.evaluation import (
    AcceptanceEvaluation,
    BackendEvaluation,
    EntityRetrievalCase,
    EntityRetrievalCorpus,
    evaluate_acceptance,
    evaluate_backend,
    format_acceptance,
    format_comparison,
    load_cases,
    load_corpus,
    seed_corpus,
)
from semantic_retrieval.factory import (
    DEFAULT_VECTOR_BACKEND,
    VECTOR_BACKENDS,
    SemanticComponents,
    SemanticRetrievalConfig,
    create_semantic_components,
)
from semantic_retrieval.indexer import IndexingStats
from semantic_retrieval.models import (
    RetrievalBackend,
    RetrievalEntityType,
    RetrievalError,
    RetrievalQuery,
    RetrievalResult,
)
from semantic_retrieval.relevance import SemanticRetrievalDecision

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RETRIEVAL_CORPUS_PATH = REPO_ROOT / "tests" / "fixtures" / "entity_retrieval_corpus.json"
DEFAULT_RETRIEVAL_CASES_PATH = REPO_ROOT / "tests" / "fixtures" / "entity_retrieval_cases.json"
EVALUATION_COLLECTIONS = ("eval_persons_semantic", "eval_events_semantic")
RETRIEVAL_BACKENDS = [
    RetrievalBackend.LEXICAL,
    RetrievalBackend.DENSE,
    RetrievalBackend.HYBRID,
    RetrievalBackend.HYBRID_RERANKED,
]


def add_semantic_arguments(subparsers: Any) -> None:
    rebuild = subparsers.add_parser(
        "rebuild-semantic-index",
        help="Build semantic documents and (re)index vectors in Qdrant",
    )
    rebuild.add_argument("--entity", choices=["person", "event", "all"], default="all")
    rebuild.add_argument("--batch-size", type=int, default=64)
    rebuild.add_argument("--limit", type=int, default=None)
    rebuild.add_argument(
        "--incremental",
        action="store_true",
        help="Keep the collection; re-embed only changed documents",
    )

    search = subparsers.add_parser(
        "semantic-search", help="Debug entity retrieval (candidates, not facts)"
    )
    search.add_argument("text")
    search.add_argument("--entity", choices=["person", "event"], default="person")
    search.add_argument(
        "--backend", choices=[b.value for b in RETRIEVAL_BACKENDS], default="hybrid"
    )
    search.add_argument("--limit", type=int, default=10)

    evaluate = subparsers.add_parser(
        "evaluate-retrieval",
        help="Evaluate lexical/dense/hybrid/reranked entity retrieval on the fixed corpus",
    )
    evaluate.add_argument(
        "--backend", choices=[*(b.value for b in RETRIEVAL_BACKENDS), "all"], default="all"
    )
    evaluate.add_argument("--k", type=int, default=5)
    evaluate.add_argument("--corpus-path", type=Path, default=DEFAULT_RETRIEVAL_CORPUS_PATH)
    evaluate.add_argument("--cases-path", type=Path, default=DEFAULT_RETRIEVAL_CASES_PATH)
    evaluate.add_argument(
        "--database-url",
        default=None,
        help="Disposable database (name ending _test/_eval); default EVALUATION_DATABASE_URL",
    )
    evaluate.add_argument(
        "--qdrant-url", default=":memory:", help="Qdrant URL or :memory: (default)"
    )
    evaluate.add_argument(
        "--vector-backend",
        choices=VECTOR_BACKENDS,
        default=DEFAULT_VECTOR_BACKEND,
        help="Vector store: qdrant (default) or pgvector (in the evaluation database)",
    )
    evaluate.add_argument(
        "--thresholds",
        default=None,
        help="Comma-separated dense similarity thresholds for relevance acceptance "
        "(default: grid over observed similarities)",
    )
    evaluate.add_argument("--output-path", type=Path, default=None)


def format_indexing_stats(stats: IndexingStats) -> str:
    return (
        f"{stats.entity_type.value}: built={stats.documents_built} embedded={stats.embedded} "
        f"unchanged={stats.unchanged} deleted={stats.deleted}"
    )


def format_search_result(
    result: RetrievalResult,
    texts: dict[int, str],
    decision: SemanticRetrievalDecision | None = None,
) -> str:
    accepted_ids = set() if decision is None else set(decision.accepted.entity_ids)
    lines = [
        (
            f"{result.backend.value} retrieval, {len(result.hits)} candidate(s). "
            "Scores rank candidates; they are not confidences of any fact."
        )
    ]
    if decision is not None:
        lines.append(
            f"Accepted as semantically relevant (dense similarity ≥ "
            f"{decision.dense_min_score:.2f}): {len(decision.accepted.hits)}; "
            f"rejected nearest neighbours: {decision.rejected_count}."
        )
    for hit in result.hits:
        components = ", ".join(f"{name}#{rank}" for name, rank in hit.component_ranks.items())
        first_line = texts.get(hit.entity_id, "").split("\n", 1)[0]
        marker = ""
        if decision is not None:
            marker = "[accepted] " if hit.entity_id in accepted_ids else "[rejected] "
        lines.append(
            f"{hit.rank:>3}. {marker}{hit.entity_type.value} #{hit.entity_id} score={hit.score:.4f}"
            + (f" [{components}]" if components else "")
            + (f" {first_line}" if first_line else "")
        )
    return "\n".join(lines)


def run_retrieval_evaluation(
    *,
    components: SemanticComponents,
    corpus: EntityRetrievalCorpus,
    cases: Sequence[EntityRetrievalCase],
    backends: Sequence[RetrievalBackend],
    k: int,
    thresholds: Sequence[float] | None = None,
) -> RetrievalEvaluationRun:
    """Seed the corpus into the (already truncated) database, index it, evaluate.

    Ranking is evaluated per backend (negative cases excluded); relevance
    acceptance is swept over thresholds on the workflow's hybrid pools.
    """
    ids = seed_corpus(components.session_factory, corpus)
    indexer = components.indexer()
    for entity_type in RetrievalEntityType:
        indexer.rebuild(entity_type)
    ranking = [
        evaluate_backend(
            backend=backend, retriever=components.retriever(backend), cases=cases, ids=ids, k=k
        )
        for backend in backends
    ]
    acceptance = evaluate_acceptance(
        retriever=components.retriever(RetrievalBackend.HYBRID),
        cases=cases,
        ids=ids,
        thresholds=thresholds,
        default_threshold=components.dense_min_score,
    )
    return RetrievalEvaluationRun(
        embedding_model_id=components.embedder.model_id, ranking=ranking, acceptance=acceptance
    )


class RetrievalEvaluationRun(BaseModel):
    embedding_model_id: str
    ranking: list[BackendEvaluation]
    acceptance: AcceptanceEvaluation


def format_evaluation_run(run: RetrievalEvaluationRun) -> str:
    return "\n".join(
        [
            "## Ranking",
            "",
            format_comparison(run.ranking),
            "",
            f"## Relevance acceptance (hybrid pools, {run.embedding_model_id})",
            "",
            (
                f"Observed dense similarity: {run.acceptance.observed_min:.3f}..."
                f"{run.acceptance.observed_max:.3f}"
            ),
            "",
            format_acceptance(run.acceptance),
        ]
    )


def run_evaluate_retrieval(args: argparse.Namespace) -> None:
    database_url = args.database_url or os.environ.get("EVALUATION_DATABASE_URL")
    if not database_url:
        raise SystemExit(
            "evaluate-retrieval needs --database-url or EVALUATION_DATABASE_URL "
            "(a disposable database whose name ends with _test or _eval)"
        )
    engine = create_database_engine(database_url)
    require_disposable_database(engine)
    truncate_disposable_tables(engine)
    backends = RETRIEVAL_BACKENDS if args.backend == "all" else [RetrievalBackend(args.backend)]
    config = SemanticRetrievalConfig(
        qdrant_url=args.qdrant_url,
        person_collection=EVALUATION_COLLECTIONS[0],
        event_collection=EVALUATION_COLLECTIONS[1],
        vector_backend=args.vector_backend,
    )
    components = create_semantic_components(
        create_session_factory(engine),
        config,
        with_reranker=RetrievalBackend.HYBRID_RERANKED in backends,
    )
    thresholds = [float(value) for value in args.thresholds.split(",")] if args.thresholds else None
    run = run_retrieval_evaluation(
        components=components,
        corpus=load_corpus(args.corpus_path),
        cases=load_cases(args.cases_path),
        backends=backends,
        k=args.k,
        thresholds=thresholds,
    )
    print(format_evaluation_run(run))
    if args.output_path is not None:
        args.output_path.parent.mkdir(parents=True, exist_ok=True)
        args.output_path.write_text(run.model_dump_json(indent=2) + "\n", encoding="utf-8")


def _components_from_env(
    session_factory: sessionmaker[Session], *, reranker: bool
) -> SemanticComponents:
    config = SemanticRetrievalConfig.from_env()
    if not config.enabled:
        raise SystemExit(
            "QDRANT_URL is not set (docker compose --profile semantic up -d), "
            "or set SEMANTIC_VECTOR_BACKEND=pgvector"
        )
    return create_semantic_components(session_factory, config, with_reranker=reranker)


SEMANTIC_EXIT_UNAVAILABLE = 2


def run_semantic_command(args: argparse.Namespace, session_factory: sessionmaker[Session]) -> bool:
    """Handle a semantic command; False when `args.command` is not one.

    Retrieval failures exit with code 2 and a message, never a traceback or an
    empty result.
    """
    try:
        return _run_semantic_command(args, session_factory)
    except RetrievalError as exc:
        print(f"Semantic retrieval unavailable [{type(exc).__name__}]: {exc}", file=sys.stderr)
        raise SystemExit(SEMANTIC_EXIT_UNAVAILABLE) from None


def _run_semantic_command(args: argparse.Namespace, session_factory: sessionmaker[Session]) -> bool:
    if args.command == "rebuild-semantic-index":
        indexer = _components_from_env(session_factory, reranker=False).indexer()
        entity_types = (
            list(RetrievalEntityType)
            if args.entity == "all"
            else [RetrievalEntityType(args.entity)]
        )
        for entity_type in entity_types:
            stats = indexer.rebuild(
                entity_type,
                batch_size=args.batch_size,
                limit=args.limit,
                incremental=args.incremental,
            )
            print(format_indexing_stats(stats))
        return True

    if args.command == "semantic-search":
        backend = RetrievalBackend(args.backend)
        components = _components_from_env(
            session_factory, reranker=backend is RetrievalBackend.HYBRID_RERANKED
        )
        entity_type = RetrievalEntityType(args.entity)
        query = RetrievalQuery(text=args.text, entity_type=entity_type, limit=args.limit)
        result = components.retriever(backend).retrieve(query)
        texts = SqlAlchemySemanticDocumentRepository(session_factory).get_texts(
            entity_type, result.entity_ids
        )
        print(
            format_search_result(result, texts, components.relevance_policy().accept(query, result))
        )
        return True

    return False
