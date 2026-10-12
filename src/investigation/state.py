"""The state an investigation carries from node to node.

Every value is a model of `investigation.models`, a plain value, or a plain list of
them, so the state is serializable as it stands: a run is stored and resumed from it.
`found`, `done`, `usage` and `warnings` are written by nodes that run in parallel;
their reducers add what each branch returns.
"""

from __future__ import annotations

import operator
from collections.abc import Mapping
from typing import Annotated, Any, TypedDict

from investigation.models import (
    CoverageAssessment,
    EvidenceItem,
    InvestigationIntent,
    InvestigationPlan,
    InvestigationQuery,
    InvestigationResult,
    Limits,
    ScenarioName,
    SearchTask,
    Subject,
    SubjectRef,
    Usage,
)


def add_usage(left: Usage, right: Usage) -> Usage:
    return left + right


class InvestigationState(TypedDict, total=False):
    question: str
    # Set when the run is stored.
    run_id: str | None
    limits: Limits
    # Only local retrievers are asked (`--local-only`, `--no-web`).
    local_only: bool
    # The sources the run keeps to (`--source`); empty: every allowed one.
    sources: list[str]
    # A scenario the request names; None: the question decides.
    requested_scenario: ScenarioName | None
    # Wall-clock seconds: when the run started and by when it must end.
    started_at: float
    deadline: float
    # Started from a stored run: the searches already made are not made again.
    resumed: bool
    intent: InvestigationIntent | None
    subject_ref: SubjectRef | None
    subject: Subject | None
    plan: InvestigationPlan | None
    # The searches of the round about to run.
    tasks: list[SearchTask]
    # The fingerprints of every search made, by any round.
    done: Annotated[list[str], operator.add]
    # The queries a retriever answered (not the ones that failed, timed out or were cut):
    # a scenario tells «asked and nobody was found» from «never asked» by it.
    answered: Annotated[list[InvestigationQuery], operator.add]
    # Everything the searches returned, as it came.
    found: Annotated[list[EvidenceItem], operator.add]
    # `found` without repeats, ranked, capped and numbered: what findings cite.
    evidence: list[EvidenceItem]
    coverage: CoverageAssessment | None
    usage: Annotated[Usage, add_usage]
    warnings: Annotated[list[str], operator.add]
    result: InvestigationResult


class SearchBranch(TypedDict):
    """What one parallel branch of the search is sent."""

    task: SearchTask
    run_id: str | None
    scenario: str
    deadline: float
    search_timeout: float


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
