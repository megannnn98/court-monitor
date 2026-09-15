"""CLI handler for `evaluate-real-world`."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any

from sqlalchemy.exc import OperationalError

from db.database import create_database_engine
from db.maintenance import NotDisposableDatabaseError
from evaluation.real_world.cli_helpers import EXIT_INFRASTRUCTURE_ERROR, corpus_texts
from evaluation.real_world.corpus_cache import RawCorpusCache
from evaluation.real_world.evaluator import (
    EvaluationDataError,
    EvaluationInputs,
    EvaluationOptions,
    default_rf_snapshot,
    qdrant_url_from_env,
    run_evaluation,
    validate_inputs,
)
from evaluation.real_world.golden import DEFAULT_GOLDEN_DIR, GoldenSplit, load_golden_dataset
from evaluation.real_world.models import (
    DEFAULT_CACHE_DIR,
    DEFAULT_MANIFEST_PATH,
    DEFAULT_REPORT_DIR,
    load_manifest,
)
from evaluation.real_world.namesake import load_namesakes
from evaluation.real_world.policy import DEFAULT_POLICY_PATH, load_policy
from evaluation.real_world.replay import CorpusCacheMissError
from evaluation.real_world.report import write_reports
from evaluation.real_world.research_eval import load_research_queries
from evaluation.real_world.retrieval_eval import load_retrieval_queries
from observability import configure_logging


def add_evaluate_arguments(subparsers: Any) -> None:
    evaluate = subparsers.add_parser(
        "evaluate-real-world", help="Real-world validation: corpus run, metrics, gates, reports"
    )
    evaluate.add_argument("--split", choices=["dev", "validation", "test", "all"], default="dev")
    evaluate.add_argument(
        "--full",
        action="store_true",
        help="Also repeated runs, failure injection and the manual review scenario",
    )
    evaluate.add_argument("--verified-only", action="store_true")
    evaluate.add_argument(
        "--semantic-model",
        action="store_true",
        help="Use the project's embedding model with Qdrant for the semantic benchmark",
    )
    evaluate.add_argument("--qdrant-url", default=None, help="default QDRANT_TEST_URL")
    evaluate.add_argument(
        "--llm-intake",
        action="store_true",
        help="Evaluate natural-language intake with Together AI (needs TOGETHER_API_KEY)",
    )
    evaluate.add_argument("--database-url", default=None, help="default EVALUATION_DATABASE_URL")
    evaluate.add_argument("--golden-dir", type=Path, default=DEFAULT_GOLDEN_DIR)
    evaluate.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    evaluate.add_argument("--manifest-path", type=Path, default=DEFAULT_MANIFEST_PATH)
    evaluate.add_argument("--policy-path", type=Path, default=DEFAULT_POLICY_PATH)
    evaluate.add_argument("--output-dir", type=Path, default=DEFAULT_REPORT_DIR)
    evaluate.add_argument(
        "--no-fail-on-gates", action="store_true", help="Exit 0 when only gates/targets fail"
    )


def run_evaluate_command(args: argparse.Namespace) -> None:
    database_url = args.database_url or os.environ.get("EVALUATION_DATABASE_URL")
    if not database_url:
        print(
            "evaluate-real-world needs --database-url or EVALUATION_DATABASE_URL (name ending _eval/_test)"
        )
        raise SystemExit(EXIT_INFRASTRUCTURE_ERROR)
    configure_logging()
    try:
        manifest = load_manifest(args.manifest_path)
        cache = RawCorpusCache(args.cache_dir)
        golden = load_golden_dataset(args.golden_dir)
        policy, policy_hash = load_policy(args.policy_path)
        inputs = EvaluationInputs(
            manifest=manifest,
            manifest_path=args.manifest_path,
            cache=cache,
            golden=golden,
            policy=policy,
            policy_hash=policy_hash,
            rf_snapshot=default_rf_snapshot(golden),
            namesakes=load_namesakes(),
            retrieval_queries=load_retrieval_queries(),
            research_queries=load_research_queries(),
        )
        validate_inputs(inputs, corpus_texts(cache, manifest))
    except (EvaluationDataError, CorpusCacheMissError, ValueError, OSError) as exc:
        print(f"evaluation data error: {exc}")
        raise SystemExit(EXIT_INFRASTRUCTURE_ERROR) from None

    llm_parser = None
    llm_reason: str | None = "natural-language intake not requested (--llm-intake)"
    if args.llm_intake:
        try:
            from llm.together_client import TogetherConfig, TogetherStructuredLlmClient
            from research.workflow.intake import LlmResearchRequestParser

            llm_parser = LlmResearchRequestParser(
                TogetherStructuredLlmClient(TogetherConfig.from_env())
            )
            llm_reason = None
        except Exception as exc:  # noqa: BLE001 - configuration problem is reported as NOT_RUN
            llm_reason = f"Together AI not configured: {exc}"
    options = EvaluationOptions(
        split=None if args.split == "all" else GoldenSplit(args.split),
        verified_only=args.verified_only,
        full=args.full,
        semantic_model=args.semantic_model,
        qdrant_url=args.qdrant_url or qdrant_url_from_env(),
        llm_parser=llm_parser,
        llm_not_run_reason=llm_reason,
    )
    engine = create_database_engine(database_url)
    try:
        report = run_evaluation(engine, inputs, options)
    except (OperationalError, NotDisposableDatabaseError) as exc:
        print(f"evaluation infrastructure error: {exc}")
        raise SystemExit(EXIT_INFRASTRUCTURE_ERROR) from None
    finally:
        engine.dispose()
    json_path, markdown_path = write_reports(report, args.output_dir)
    for gate in report.safety_gates:
        print(
            f"{gate.status.value:7} {gate.kind.value:8} {gate.name}={gate.value} ({gate.comparator} {gate.threshold})"
        )
    print(f"overall_status={report.overall_status.value} exit_code={report.exit_code}")
    for reason in report.status_reasons:
        print(f"- {reason}")
    print(f"report: {json_path} {markdown_path}")
    if report.exit_code and not args.no_fail_on_gates:
        raise SystemExit(report.exit_code)
