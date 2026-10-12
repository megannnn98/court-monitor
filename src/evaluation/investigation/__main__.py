"""`python -m evaluation.investigation build|retrieval|scenarios`.

Every command reads the database read-only (`--database-url`, default DATABASE_URL);
the golden set and the reports go to `var/investigation_eval/` (ignored: real names).
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from evaluation.investigation import golden as golden_set
from evaluation.investigation import retrieval, scenarios

OUT = Path("var/investigation_eval")
READ_ONLY = {"options": "-c default_transaction_read_only=on -c statement_timeout=60000"}


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m evaluation.investigation")
    parser.add_argument("command", choices=["build", "retrieval", "scenarios"])
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    parser.add_argument("--golden", type=Path, default=OUT / "golden.json")
    parser.add_argument("--dense-model", default=None, help="e.g. intfloat/multilingual-e5-base")
    args = parser.parse_args()
    if not args.database_url:
        raise SystemExit("--database-url or DATABASE_URL is required")
    engine = create_engine(args.database_url, connect_args=READ_ONLY)
    factory = sessionmaker(engine)
    if args.command == "build":
        with factory() as session:
            built = golden_set.build(session)
        built.save(args.golden)
        print(json.dumps(golden_set.summary(built), ensure_ascii=False))
        return
    loaded = golden_set.GoldenSet.load(args.golden)
    if args.command == "retrieval":
        with factory() as session:
            table = retrieval.compare(session, loaded, dense_model=args.dense_model, cache=OUT)
        (OUT / "retrieval.json").write_text(json.dumps(table, indent=1), encoding="utf-8")
        print(retrieval.as_markdown(table))
        return
    report = scenarios.evaluate(factory, loaded, dense_model=args.dense_model)
    name = "scenarios-dense.json" if args.dense_model else "scenarios.json"
    (OUT / name).write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
