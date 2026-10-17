"""`evaluate-final`: the deterministic product-level evaluation (ADR 0014)."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any

from db.database import create_database_engine
from evaluation.final.corpus import DEFAULT_CORPUS_PATH, load_final_corpus
from evaluation.final.report import build_report, write_reports
from evaluation.final.runner import FinalEvaluationRunner
from observability import configure_logging

GATES_FAILED_EXIT = 1


def add_final_evaluation_arguments(subparsers: Any) -> None:
    parser = subparsers.add_parser(
        "evaluate-final",
        help="Run the final system evaluation on a disposable database",
    )
    parser.add_argument(
        "--database-url",
        default=None,
        help="Disposable database (name ends with _test or _eval); default EVALUATION_DATABASE_URL",
    )
    parser.add_argument("--corpus-path", type=Path, default=DEFAULT_CORPUS_PATH)
    parser.add_argument("--output-dir", type=Path, default=Path("reports"))
    parser.add_argument(
        "--no-fail-on-gates",
        action="store_true",
        help="Exit 0 even when a safety gate or quality floor fails",
    )


def run_final_evaluation_command(args: argparse.Namespace) -> None:
    database_url = args.database_url or os.environ.get("EVALUATION_DATABASE_URL")
    if not database_url:
        raise SystemExit(
            "evaluate-final needs --database-url or EVALUATION_DATABASE_URL "
            "(a disposable database whose name ends with _test or _eval)"
        )
    configure_logging()
    engine = create_database_engine(database_url)
    try:
        corpus = load_final_corpus(args.corpus_path)
        cases = FinalEvaluationRunner(engine).run(corpus)
    finally:
        engine.dispose()
    report = build_report(corpus, cases)
    json_path, markdown_path = write_reports(report, args.output_dir)
    for gate in report.gates:
        print(f"{'PASS' if gate.passed else 'FAIL'} {gate.name}={gate.value} ({gate.threshold})")
    print(f"fully_correct_case_rate={report.metrics['fully_correct_case_rate']}")
    print(f"report: {json_path} {markdown_path}")
    if not report.gates_passed and not args.no_fail_on_gates:
        raise SystemExit(GATES_FAILED_EXIT)
