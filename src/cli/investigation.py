"""`investigate`: one question — about a named person, or about whom a publication does
not name — answered with the evidence behind every fact and what is still missing."""

from __future__ import annotations

import argparse
import os

from application import (
    build_investigation_service,
    build_processing_service,
    dense_embedder_from_env,
)
from cli.context import CliContext
from investigation.models import SCENARIOS, InvestigationResult
from investigation.service import (
    RunBusyError,
    RunNotFoundError,
    RunStoreError,
    UnknownSourceError,
)
from observability import configure_logging


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser(
        "investigate",
        help=(
            "Collect what is known of a person, or who an unnamed figurant may be, with "
            "the evidence, and say what is missing"
        ),
    )
    parser.add_argument(
        "question",
        nargs="?",
        help=(
            "The question in plain words: it names a person, or a publication's id "
            "(«Установи фигуранта публикации 12345»)"
        ),
    )
    parser.add_argument("--json", action="store_true", help="Print the whole result as JSON")
    parser.add_argument(
        "--max-followups",
        type=int,
        default=None,
        help="Follow-up rounds for missing facts (at most INVESTIGATION_MAX_FOLLOWUPS)",
    )
    parser.add_argument(
        "--local-only",
        "--no-web",
        dest="local_only",
        action="store_true",
        help="Ask only sources that read what is already stored here",
    )
    parser.add_argument(
        "--source",
        dest="sources",
        action="append",
        default=[],
        metavar="NAME",
        help=(
            "Keep to this source (repeatable): local, telegram, or one retriever "
            "(publications_fts, telegram:tg-ovdinfolive, …)"
        ),
    )
    parser.add_argument(
        "--scenario", choices=SCENARIOS, default=None, help="Which kind of question it is"
    )
    parser.add_argument(
        "--no-llm", action="store_true", help="Rules alone: no model reads or words anything"
    )
    parser.add_argument(
        "--run-id", default=None, help="Print a stored run's result instead of asking anew"
    )
    parser.add_argument(
        "--resume", default=None, metavar="RUN_ID", help="Carry on a run that was interrupted"
    )
    parser.set_defaults(handler=run_investigate)

    index = subparsers.add_parser(
        "index-publications",
        help=(
            "Embed the stored publications for the investigation's dense retrieval "
            "(INVESTIGATION_DENSE_MODEL; needs the optional semantic group)"
        ),
    )
    index.set_defaults(handler=run_index_publications)


def _lines(result: InvestigationResult) -> list[str]:
    lines = [f"Статус: {result.status}"]
    if result.run_id:
        lines.append(f"Запуск: {result.run_id}")
    if result.subject is not None:
        subject = result.subject
        known_as = (
            subject.candidates[0].name
            if subject.entity_key and subject.candidates
            else subject.name
        )
        lines.append(f"О ком: {known_as}")
    if result.model_summary:
        lines += ["", "Сводка модели (не проверена):", result.model_summary]
    if result.findings:
        lines += ["", "Установлено:"]
        lines += [
            f"  - {finding.statement} [{', '.join(finding.evidence)}]"
            for finding in result.findings
        ]
    if result.candidates:
        lines += ["", "Кандидаты (оценка по признакам, не установленная личность):"]
        for candidate in result.candidates:
            signals = ", ".join(
                f"{signal.signal} +{signal.weight:g} [{', '.join(signal.evidence)}]"
                for signal in candidate.signals
            )
            lines.append(
                f"  - {candidate.name} ({candidate.kind}): {candidate.score:g} — {signals}"
            )
    for title, items in (
        ("Не хватает", result.missing_information),
        ("Противоречия", result.conflicts),
        ("Замечания", result.warnings),
    ):
        if items:
            lines += ["", f"{title}:"] + [f"  - {item}" for item in items]
    if result.evidence:
        lines += ["", "Материалы:"]
        for item in result.evidence:
            where = item.url or item.entity_key or item.source_id
            lines.append(f"  [{item.ref}] {' '.join(item.text.split())[:160]} ({where})")
    usage = result.usage
    lines += [
        "",
        (
            f"Поисков: {usage.search_calls} (в базе {usage.local_searches}, вне её "
            f"{usage.network_searches}, не ответили {usage.failed_searches}), "
            f"дополнительных раундов: {usage.followup_rounds}, обращений к модели: "
            f"{usage.llm_calls}, стоимость: ${usage.cost_usd:.4f}, {usage.elapsed_ms} мс"
        ),
    ]
    return lines


def run_investigate(args: argparse.Namespace, context: CliContext) -> None:
    configure_logging()
    if args.max_followups is not None and args.max_followups < 0:
        raise SystemExit("--max-followups must not be negative")
    asked = [args.question is not None, args.run_id is not None, args.resume is not None]
    if sum(asked) != 1:
        raise SystemExit("give a question, or --run-id, or --resume — exactly one")
    env = dict(os.environ)
    if args.no_llm:
        env["INVESTIGATION_MAX_LLM_CALLS"] = "0"
    try:
        service = build_investigation_service(context.session_factory, env=env)
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
    try:
        if args.run_id is not None:
            stored = service.get(args.run_id)
            if stored is None or stored.result is None:
                state = "not found" if stored is None else f"not finished ({stored.status})"
                raise SystemExit(f"run {args.run_id}: {state}")
            result = InvestigationResult.model_validate(stored.result)
        elif args.resume is not None:
            if args.scenario is not None:
                raise SystemExit("--scenario cannot change a stored run: it has its own")
            # A resumed run is held to these as a new one is; none of them is ignored.
            result = service.resume_sync(
                args.resume,
                max_followups=args.max_followups,
                local_only=args.local_only,
                sources=args.sources,
            )
        else:
            result = service.investigate_sync(
                args.question,
                max_followups=args.max_followups,
                local_only=args.local_only,
                sources=args.sources,
                scenario=args.scenario,
            )
    except UnknownSourceError as exc:
        raise SystemExit(str(exc)) from None
    except RunNotFoundError as exc:
        raise SystemExit(f"run {exc}: not found") from None
    except RunBusyError as exc:
        raise SystemExit(f"run {exc}: another process works on it now") from None
    except RunStoreError as exc:
        raise SystemExit(
            f"run {exc}: the store did not take the run's limits; nothing was searched"
        ) from None
    if args.json:
        print(result.model_dump_json(indent=2))
    else:
        print("\n".join(_lines(result)))
    if result.status == "failed":
        raise SystemExit(1)


def run_index_publications(args: argparse.Namespace, context: CliContext) -> None:
    configure_logging()
    embedder = dense_embedder_from_env()
    if embedder is None:
        raise SystemExit(
            "set INVESTIGATION_DENSE_MODEL and install the semantic group: uv sync --group semantic"
        )
    result = build_processing_service(context.session_factory).index_publication_vectors(embedder)
    print(
        f"articles: {result.articles}, indexed: {result.indexed}, "
        f"unchanged: {result.unchanged}, windows: {result.windows}"
    )
