"""`python -m evaluation.investigation build|retrieval|scenarios|web`.

Every command reads the database read-only (`--database-url`, default DATABASE_URL);
the golden set and the reports go to `var/investigation_eval/` (ignored: real names).

`web` asks the golden questions two ways — `local + Telegram`, and the same plus Tavily —
and measures what a reader gets against what it costs. The second mode spends real
credits, so it is asked for by name (`--modes local web`), never by default.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from evaluation.investigation import golden as golden_set
from evaluation.investigation import manual, retrieval, scenarios, web_benchmark
from observability import configure_logging

OUT = Path("var/investigation_eval")
READ_ONLY = {"options": "-c default_transaction_read_only=on -c statement_timeout=60000"}


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m evaluation.investigation")
    parser.add_argument("command", choices=["build", "retrieval", "scenarios", "web", "manual"])
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    parser.add_argument("--golden", type=Path, default=OUT / "golden.json")
    parser.add_argument(
        "--manual",
        type=Path,
        default=OUT / "manual_golden.json",
        help="the hand-annotated set, with an expected fact per case and its source",
    )
    parser.add_argument(
        "--kinds",
        nargs="+",
        default=["persons", "unnamed", "namesakes"],
        choices=["persons", "unnamed", "namesakes"],
    )
    parser.add_argument(
        "--modes",
        nargs="+",
        default=["local"],
        choices=["local", "web"],
        help="Which registry each run gets; `web` spends Tavily credits",
    )
    parser.add_argument("--persons", type=int, default=30)
    parser.add_argument("--unnamed", type=int, default=10)
    parser.add_argument(
        "--with-llm",
        action="store_true",
        help="Ask the model of the environment for the intake and the summary",
    )
    args = parser.parse_args()
    # The benchmark asks the same services the product does, so it logs what they log —
    # among it what a search cost, which is the one number a run cannot vouch for itself.
    configure_logging()
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
    if args.command == "manual":
        document = manual.load(args.manual)
        reports = [
            manual.run(
                factory,
                document,
                use_web=mode == "web",
                with_model=args.with_llm,
                kinds=args.kinds,
            )
            for mode in args.modes
        ]
        written = {
            "modes": args.modes,
            "with_llm": args.with_llm,
            "reports": reports,
            "summary": manual.summary(reports),
        }
        if len(reports) == 2:
            written["displacement"] = manual.displacement(reports[0], reports[1])
            written["fact_table"] = manual.fact_table(reports[0], reports[1])
        (OUT / "manual_benchmark.json").write_text(
            json.dumps(written, indent=1, ensure_ascii=False), encoding="utf-8"
        )
        print(json.dumps(written["summary"], indent=1, ensure_ascii=False))
        if "fact_table" in written:
            print(json.dumps(written["fact_table"], indent=1, ensure_ascii=False))
            print(json.dumps(written["displacement"], indent=1, ensure_ascii=False))
        return
    loaded = golden_set.GoldenSet.load(args.golden)
    if args.command == "web":
        persons = _cases(loaded.persons, args.persons)
        unnamed = _cases(loaded.unnamed, args.unnamed)
        reports = [
            web_benchmark.run(
                factory,
                loaded,
                use_web=mode == "web",
                persons=persons,
                unnamed=unnamed,
                with_model=args.with_llm,
            )
            for mode in args.modes
        ]
        document = {
            "modes": args.modes,
            "with_llm": args.with_llm,
            "reports": reports,
            "summary": [web_benchmark.summarise(report) for report in reports],
        }
        (OUT / "web_benchmark.json").write_text(
            json.dumps(document, indent=1, ensure_ascii=False), encoding="utf-8"
        )
        table = web_benchmark.as_markdown(reports)
        (OUT / "web_benchmark.md").write_text(table + "\n", encoding="utf-8")
        print(table)
        if len(reports) == 2:
            print(json.dumps(web_benchmark.delta(reports[0], reports[1]), indent=1))
            print(json.dumps(web_benchmark.displaced(reports[0], reports[1])["per_case"], indent=1))
        return
    if args.command == "retrieval":
        with factory() as session:
            compared = retrieval.compare(session, loaded)
        (OUT / "retrieval.json").write_text(json.dumps(compared, indent=1), encoding="utf-8")
        print(retrieval.as_markdown(compared))
        return
    measured = scenarios.evaluate(factory, loaded)
    (OUT / "scenarios.json").write_text(
        json.dumps(measured, indent=1, ensure_ascii=False), encoding="utf-8"
    )
    print(json.dumps(measured, indent=1, ensure_ascii=False))


def _cases[T](all_cases: list[T], count: int) -> list[T]:
    """`count` cases of a kind, taken round the forms they come in (the golden set holds
    the four forms of every name one after another), so a short run keeps every form."""
    if count >= len(all_cases):
        return list(all_cases)
    every = max(len(all_cases) // 4, 1)
    buckets = [
        all_cases[start : start + every]
        for start in range(0, len(all_cases), every)
        if all_cases[start : start + every]
    ]
    picked: list[T] = []
    depth = 0
    while len(picked) < count and any(depth < len(bucket) for bucket in buckets):
        for bucket in buckets:
            if depth < len(bucket) and len(picked) < count:
                picked.append(bucket[depth])
        depth += 1
    return picked


if __name__ == "__main__":
    main()
