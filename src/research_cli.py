"""Developer-facing CLI adapter over ResearchService.

Only argument parsing and output formatting live here; all filtering and
review semantics belong to ResearchService.
"""

from __future__ import annotations

import argparse
from datetime import date
from typing import Any

from pydantic import ValidationError

from candidate_query_models import RosfinmonitoringStatus
from extraction_models import EventType
from persecution_models import PersecutionClassificationStatus
from research_models import (
    DEFAULT_RESEARCH_LIMIT,
    PersonResearchCriteria,
    ResearchObjectType,
    ResearchRequest,
    ResearchResponse,
)
from source_registry import SOURCES, get_source_definition


class ResearchCliError(Exception):
    pass


def add_research_arguments(subparsers: Any) -> None:
    parser = subparsers.add_parser(
        "research",
        help="Run a structured, deterministic research request",
    )
    parser.add_argument(
        "--object",
        choices=[t.value for t in ResearchObjectType],
        default=ResearchObjectType.PERSON.value,
    )
    parser.add_argument("--person-id", type=int, default=None)
    parser.add_argument("--name", default=None, help="Substring of name or alias")
    parser.add_argument(
        "--persecution-status",
        choices=[s.value for s in PersecutionClassificationStatus],
        default=None,
    )
    parser.add_argument("--min-confidence", type=float, default=None)
    parser.add_argument(
        "--rosfin-status",
        choices=[s.value for s in RosfinmonitoringStatus],
        default=None,
    )
    parser.add_argument("--snapshot-id", type=int, default=None)
    parser.add_argument(
        "--event-type",
        action="append",
        choices=[t.value for t in EventType],
        default=None,
        help="Repeatable",
    )
    parser.add_argument("--date-from", type=date.fromisoformat, default=None)
    parser.add_argument("--date-to", type=date.fromisoformat, default=None)
    parser.add_argument("--source", choices=sorted(SOURCES), default=None)
    parser.add_argument("--limit", type=int, default=DEFAULT_RESEARCH_LIMIT)
    parser.add_argument("--json", action="store_true", help="Print ResearchResponse JSON")


def build_research_request(args: argparse.Namespace) -> ResearchRequest:
    try:
        return ResearchRequest(
            object_type=ResearchObjectType(args.object),
            criteria=PersonResearchCriteria(
                person_id=args.person_id,
                name=args.name,
                persecution_status=args.persecution_status,
                persecution_min_confidence=args.min_confidence,
                rosfinmonitoring_status=args.rosfin_status,
                snapshot_id=args.snapshot_id,
                event_types=args.event_type,
                date_from=args.date_from,
                date_to=args.date_to,
                source=(
                    get_source_definition(args.source).source_name
                    if args.source is not None
                    else None
                ),
            ),
            limit=args.limit,
        )
    except ValidationError as exc:
        messages = "; ".join(
            f"{'.'.join(str(part) for part in error['loc']) or 'request'}: {error['msg']}"
            for error in exc.errors()
        )
        raise ResearchCliError(f"Invalid research request: {messages}") from None


def format_research_response(response: ResearchResponse) -> str:
    lines = [f"Matched {response.total_matched} person(s), showing {len(response.results)}"]
    for result in response.results:
        lines.append("")
        lines.append(f"#{result.person.id} {result.person.canonical_name}")

        persecution = result.persecution
        lines.append(
            f"  persecution: {persecution.status.value} ({persecution.confidence:.2f})"
            if persecution is not None
            else "  persecution: not classified"
        )

        rf = result.rosfinmonitoring
        if rf is not None:
            confidence = f" ({rf.confidence:.2f})" if rf.confidence is not None else ""
            lines.append(
                f"  rosfinmonitoring[snapshot {rf.snapshot_id}]: {rf.status.value}{confidence}"
            )

        lines.append(f"  review_required: {'yes' if result.review_required else 'no'}")
        for warning in result.warnings:
            lines.append(f"    ! {warning.code.value}: {warning.message}")

        lines.append(f"  events ({len(result.events)}):")
        for event in result.events:
            event_date = event.event_date.date().isoformat() if event.event_date else "unknown-date"
            roles = ",".join(role.value for role in event.roles)
            lines.append(
                f"    {event_date} {event.event_type.value} [{roles}] article {event.article_id}"
            )

        lines.append(f"  sources ({len(result.sources)}):")
        for source in result.sources:
            lines.append(
                f"    [{source.article_id}] {source.source_name}: "
                f"{source.article_title} {source.url}"
            )
    return "\n".join(lines)
