"""`evaluate-er`: ER v2 on the golden corpus, in a disposable database."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any

from db.database import create_database_engine, create_session_factory
from db.maintenance import require_disposable_database, truncate_disposable_tables
from evaluation.person_resolution.er_run import (
    format_er_evaluation,
    load_er_corpus,
    run_er_evaluation,
)
from persons.resolution.candidates import CandidateConfig
from persons.resolution.decision import ResolutionThresholds

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_ER_CORPUS_PATH = REPO_ROOT / "tests" / "fixtures" / "er_v2_corpus.json"


def add_er_evaluation_arguments(subparsers: Any) -> None:
    evaluate = subparsers.add_parser(
        "evaluate-er",
        help="Evaluate ER v2 on the golden corpus in a disposable database",
    )
    evaluate.add_argument(
        "--database-url",
        default=None,
        help="Disposable database (name ends with _test or _eval); default EVALUATION_DATABASE_URL",
    )
    evaluate.add_argument("--corpus-path", type=Path, default=DEFAULT_ER_CORPUS_PATH)
    evaluate.add_argument("--sweep", action="store_true", help="Sweep decision thresholds")
    evaluate.add_argument("--output-path", type=Path, default=None)


def run_evaluate_er(args: argparse.Namespace) -> None:
    database_url = args.database_url or os.environ.get("EVALUATION_DATABASE_URL")
    if not database_url:
        raise SystemExit(
            "evaluate-er needs --database-url or EVALUATION_DATABASE_URL "
            "(a disposable database whose name ends with _test or _eval)"
        )
    engine = create_database_engine(database_url)
    require_disposable_database(engine)
    truncate_disposable_tables(engine)
    session_factory = create_session_factory(engine)
    config = CandidateConfig.from_env(os.environ)
    run = run_er_evaluation(
        session_factory,
        load_er_corpus(args.corpus_path),
        thresholds=ResolutionThresholds.from_env(os.environ),
        config=config,
        sweep=args.sweep,
    )
    print(format_er_evaluation(run))
    if args.output_path is not None:
        args.output_path.parent.mkdir(parents=True, exist_ok=True)
        args.output_path.write_text(run.model_dump_json(indent=2) + "\n", encoding="utf-8")
