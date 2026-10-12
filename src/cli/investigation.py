"""`investigate`: one question about a person, answered from what is stored, with the
evidence behind every fact and what is still missing."""

from __future__ import annotations

import argparse

from application import build_investigation_service
from cli.context import CliContext
from investigation.models import InvestigationResult
from observability import configure_logging


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser(
        "investigate",
        help=(
            "Collect what is known of a person or a case, with its evidence, and say what "
            "is missing"
        ),
    )
    parser.add_argument("question", help="The question in plain words; it must name a person")
    parser.add_argument("--json", action="store_true", help="Print the whole result as JSON")
    parser.add_argument(
        "--max-followups",
        type=int,
        default=None,
        help="Follow-up rounds for missing facts (at most INVESTIGATION_MAX_FOLLOWUPS)",
    )
    # Both say the same today: every retriever shipped reads the local database.
    parser.add_argument(
        "--local-only",
        "--no-web",
        dest="local_only",
        action="store_true",
        help="Ask only sources that read what is already stored here",
    )
    parser.set_defaults(handler=run_investigate)


def _lines(result: InvestigationResult) -> list[str]:
    lines = [f"Статус: {result.status}"]
    if result.subject is not None:
        subject = result.subject
        known_as = subject.candidates[0].name if subject.entity_key else subject.name
        lines.append(f"О ком: {known_as}")
    if result.model_summary:
        lines += ["", "Сводка модели (не проверена):", result.model_summary]
    if result.findings:
        lines += ["", "Установлено:"]
        lines += [
            f"  - {finding.statement} [{', '.join(finding.evidence)}]"
            for finding in result.findings
        ]
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
            f"Поисков: {usage.search_calls}, дополнительных раундов: {usage.followup_rounds}, "
            f"обращений к модели: {usage.llm_calls}"
        ),
    ]
    return lines


def run_investigate(args: argparse.Namespace, context: CliContext) -> None:
    configure_logging()
    if args.max_followups is not None and args.max_followups < 0:
        raise SystemExit("--max-followups must not be negative")
    try:
        service = build_investigation_service(context.session_factory)
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
    result = service.investigate_sync(
        args.question, max_followups=args.max_followups, local_only=args.local_only
    )
    if args.json:
        print(result.model_dump_json(indent=2))
    else:
        print("\n".join(_lines(result)))
    if result.status == "failed":
        raise SystemExit(1)
