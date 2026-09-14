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

from sqlalchemy.orm import Session, sessionmaker

from database import create_database_engine, create_session_factory
from database_maintenance import require_disposable_database, truncate_disposable_tables
from semantic_retrieval.document_store import SqlAlchemySemanticDocumentRepository
from semantic_retrieval.evaluation import (
    BackendEvaluation,
    EntityRetrievalCase,
    EntityRetrievalCorpus,
    evaluate_backend,
    format_comparison,
    load_cases,
    load_corpus,
    seed_corpus,
)
from semantic_retrieval.factory import (
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

REPO_ROOT = Path(__file__).resolve().parent.parent
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
    evaluate.add_argument("--output-path", type=Path, default=None)


def format_indexing_stats(stats: IndexingStats) -> str:
    return (
        f"{stats.entity_type.value}: built={stats.documents_built} embedded={stats.embedded} "
        f"unchanged={stats.unchanged} deleted={stats.deleted}"
    )


def format_search_result(result: RetrievalResult, texts: dict[int, str]) -> str:
    lines = [
        (
            f"{result.backend.value} retrieval, {len(result.hits)} candidate(s). "
            "Scores rank candidates; they are not confidences of any fact."
        )
    ]
    for hit in result.hits:
        components = ", ".join(f"{name}#{rank}" for name, rank in hit.component_ranks.items())
        first_line = texts.get(hit.entity_id, "").split("\n", 1)[0]
        lines.append(
            f"{hit.rank:>3}. {hit.entity_type.value} #{hit.entity_id} score={hit.score:.4f}"
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
) -> list[BackendEvaluation]:
    """Seed the corpus into the (already truncated) database, index it, evaluate."""
    ids = seed_corpus(components.session_factory, corpus)
    indexer = components.indexer()
    for entity_type in RetrievalEntityType:
        indexer.rebuild(entity_type)
    return [
        evaluate_backend(
            backend=backend, retriever=components.retriever(backend), cases=cases, ids=ids, k=k
        )
        for backend in backends
    ]


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
    )
    components = create_semantic_components(
        create_session_factory(engine),
        config,
        with_reranker=RetrievalBackend.HYBRID_RERANKED in backends,
    )
    evaluations = run_retrieval_evaluation(
        components=components,
        corpus=load_corpus(args.corpus_path),
        cases=load_cases(args.cases_path),
        backends=backends,
        k=args.k,
    )
    print(format_comparison(evaluations))
    if args.output_path is not None:
        args.output_path.parent.mkdir(parents=True, exist_ok=True)
        args.output_path.write_text(
            "[\n"
            + ",\n".join(evaluation.model_dump_json(indent=2) for evaluation in evaluations)
            + "\n]\n",
            encoding="utf-8",
        )


def _components_from_env(
    session_factory: sessionmaker[Session], *, reranker: bool
) -> SemanticComponents:
    config = SemanticRetrievalConfig.from_env()
    if config.qdrant_url is None:
        raise SystemExit("QDRANT_URL is not set (docker compose --profile semantic up -d)")
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
        result = components.retriever(backend).retrieve(
            RetrievalQuery(text=args.text, entity_type=entity_type, limit=args.limit)
        )
        texts = SqlAlchemySemanticDocumentRepository(session_factory).get_texts(
            entity_type, result.entity_ids
        )
        print(format_search_result(result, texts))
        return True

    return False
