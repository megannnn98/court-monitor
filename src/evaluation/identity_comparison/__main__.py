"""Run the comparison and write its numbers.

    python -m evaluation.identity_comparison \\
        --corpus tests/fixtures/er_v2_corpus.json \\
        --server-url postgresql+psycopg://…@127.0.0.1:5434/court_monitor_test \\
        --real-url postgresql+psycopg://…@127.0.0.1:5434/junk_eval \\
        --out var/identity_comparison

`--server-url` names a server where a database may be created: ER v2 plans against the
corpus seeded into a database made for the run and dropped after it. `--real-url` is
only read. `results.json` holds counts; `disagreement_sample.json` holds names from the
real database and belongs under the ignored `var/`.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import Engine, make_url, text
from sqlalchemy.orm import Session, sessionmaker

from db.database import create_database_engine, create_session_factory
from evaluation.identity_comparison import real, synthetic
from evaluation.person_resolution.er_run import load_er_corpus, run_er_evaluation

REPO_ROOT = Path(__file__).resolve().parents[3]


def _outside_a_transaction(engine: Engine, statement: str) -> None:
    with engine.connect() as connection:
        connection.execution_options(isolation_level="AUTOCOMMIT").execute(text(statement))


@contextmanager
def _disposable_database(server_url: str) -> Iterator[sessionmaker[Session]]:
    """A database of this run's own, migrated to head, dropped whatever happens."""
    name = f"court_monitor_identity_{uuid.uuid4().hex[:12]}_eval"
    database_url = make_url(server_url).set(database=name).render_as_string(hide_password=False)
    server = create_database_engine(server_url)
    _outside_a_transaction(server, f'CREATE DATABASE "{name}"')
    engine = create_database_engine(database_url)
    try:
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=REPO_ROOT,
            env={**os.environ, "DATABASE_URL": database_url},
            capture_output=True,
            text=True,
            timeout=300,
            check=True,
        )
        yield create_session_factory(engine)
    finally:
        engine.dispose()
        _outside_a_transaction(server, f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        server.dispose()


def run_synthetic(corpus_path: Path, server_url: str) -> dict[str, object]:
    corpus = load_er_corpus(corpus_path)
    with _disposable_database(server_url) as session_factory:
        started = time.perf_counter()
        run = run_er_evaluation(session_factory, corpus)
        er_seconds = time.perf_counter() - started
    started = time.perf_counter()
    rules = [synthetic.rules_verdict(case, corpus.persons) for case in corpus.cases]
    rules_seconds = time.perf_counter() - started
    verdicts = {
        "er_v2": [synthetic.er_verdict(result) for result in run.outcomes],
        "rules": rules,
        # Not a model that runs anywhere: the rules, had three-word names been stored
        # with their parts in place. It tells the rules from the stored names.
        "rules_repaired": [
            synthetic.rules_verdict(case, corpus.persons, repaired=True) for case in corpus.cases
        ],
    }
    return {
        "corpus": str(corpus_path),
        "persons": len(corpus.persons),
        "cases": len(corpus.cases),
        "scores": {
            model: synthetic.score(corpus.cases, answers).summary()
            for model, answers in verdicts.items()
        },
        "by_tag": synthetic.by_tag(corpus.cases, verdicts),
        "cases_table": synthetic.case_table(corpus.cases, verdicts),
        "rules_on_the_corpus_persons_alone": {
            "rules": synthetic.corpus_self_grouping(corpus),
            "rules_repaired": synthetic.corpus_self_grouping(corpus, repaired=True),
        },
        "er_seconds": round(er_seconds, 2),
        "rules_seconds": round(rules_seconds, 2),
    }


def run_real(real_url: str) -> tuple[dict[str, object], list[dict[str, object]]]:
    engine = real.read_only_engine(real_url)
    try:
        rows, signatures = real.load_rows(engine)
        comparison, entity_of = real.compare(rows)
        strata, sample = real.disagreement_sample(rows, entity_of, real.person_names(engine))
        return {
            "database": make_url(real_url).database,
            "signatures_left_out": signatures,
            "er_v2_decisions": real.decision_shares(engine),
            "comparison": comparison,
            "disagreement_strata": strata,
            "growth": real.growth(rows),
        }, sample
    finally:
        engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--server-url", required=True)
    parser.add_argument("--real-url")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    results: dict[str, object] = {"synthetic": run_synthetic(args.corpus, args.server_url)}
    args.out.mkdir(parents=True, exist_ok=True)
    if args.real_url:
        results["real"], sample = run_real(args.real_url)
        (args.out / "disagreement_sample.json").write_text(
            json.dumps(sample, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    (args.out / "results.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
