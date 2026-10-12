"""The state an investigation carries from node to node.

Every value is a model of `investigation.models` or a plain list of them, so the state
is serializable as it stands. `found`, `usage` and `warnings` are written by nodes that
run in parallel; their reducers add what each branch returns.
"""

from __future__ import annotations

import operator
from collections.abc import Mapping
from typing import Annotated, Any, TypedDict

from investigation.models import (
    CoverageAssessment,
    EvidenceItem,
    InvestigationQuery,
    InvestigationResult,
    Limits,
    Subject,
    Usage,
)


def add_usage(left: Usage, right: Usage) -> Usage:
    return left + right


class InvestigationState(TypedDict, total=False):
    question: str
    limits: Limits
    # Only local retrievers are asked (`--local-only`, `--no-web`).
    local_only: bool
    subject: Subject | None
    # The queries of the round about to be searched.
    queries: list[InvestigationQuery]
    # Everything the searches returned, as it came.
    found: Annotated[list[EvidenceItem], operator.add]
    # `found` without repeats, capped and numbered: what findings cite.
    evidence: list[EvidenceItem]
    coverage: CoverageAssessment | None
    usage: Annotated[Usage, add_usage]
    warnings: Annotated[list[str], operator.add]
    result: InvestigationResult


class SearchTask(TypedDict):
    """What one parallel branch of the search is sent."""

    query: InvestigationQuery
    retriever: str


def dump_state(state: Mapping[str, Any]) -> dict[str, Any]:
    """The state as JSON-ready data."""
    dumped: dict[str, Any] = {}
    for key, value in state.items():
        if isinstance(value, list):
            dumped[key] = [
                item.model_dump(mode="json") if hasattr(item, "model_dump") else item
                for item in value
            ]
        elif hasattr(value, "model_dump"):
            dumped[key] = value.model_dump(mode="json")
        else:
            dumped[key] = value
    return dumped
