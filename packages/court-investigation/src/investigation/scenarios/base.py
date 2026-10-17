"""What a scenario of an investigation is: the facts it expects, what it asks first, how
it reads what was found, and what it would ask next.

The graph runs the lifecycle (intake → plan → search → merge → coverage → follow-up →
report) and knows no scenario's facts. A scenario knows no retriever: it says what it
looks for, and the planner gives each query the retrievers able to answer it. A third
scenario is one more object of this shape in the registry.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from investigation.models import (
    CoverageAssessment,
    EvidenceItem,
    FactRequirement,
    FigurantCandidate,
    Finding,
    InvestigationIntent,
    InvestigationQuery,
    ScenarioName,
    Status,
    Subject,
    SubjectRef,
)


class PlannedQuery(BaseModel):
    """A query a scenario wants asked, and why; the planner decides where."""

    model_config = ConfigDict(frozen=True)

    query: InvestigationQuery
    purpose: str
    priority: int = 0


@dataclass(frozen=True)
class ScenarioView:
    """What a scenario reads after a round: the intent, who the run is about, and the
    evidence merged so far (numbered, without repeats)."""

    intent: InvestigationIntent
    subject_ref: SubjectRef
    subject: Subject | None
    evidence: Sequence[EvidenceItem]
    # Everything found, before the ceiling: who answers a name is settled on it.
    found: Sequence[EvidenceItem] = field(default_factory=tuple)
    # The follow-up rounds already run.
    rounds: int = 0
    # The queries a retriever answered: «asked, nothing found» is not «never asked».
    answered: Sequence[InvestigationQuery] = field(default_factory=tuple)


class InvestigationScenario(Protocol):
    @property
    def name(self) -> ScenarioName: ...

    @property
    def facts(self) -> Mapping[str, FactRequirement]:
        """Every fact this scenario can report: what an intent may ask for."""
        ...

    def intent_by_rules(self, question: str) -> InvestigationIntent | None:
        """The intent the question states, read by rules; None if it is not this
        scenario's question."""
        ...

    def subject_of(self, intent: InvestigationIntent) -> SubjectRef | None:
        """Whom or what the run is about; None: the question names nothing to look up."""
        ...

    def subject_name(self, intent: InvestigationIntent) -> str:
        """How the result names the subject."""
        ...

    def initial_queries(
        self, intent: InvestigationIntent, subject: SubjectRef
    ) -> list[PlannedQuery]: ...

    def settle(self, view: ScenarioView) -> Subject | None:
        """Who the subject is by everything found: one entity group, candidates, none."""
        ...

    def requirements(self, view: ScenarioView) -> list[FactRequirement]: ...

    def assess(self, view: ScenarioView) -> CoverageAssessment:
        """Known, missing, conflicting, and the next round's queries for the missing
        alone. Rules: the same evidence always gives the same assessment."""
        ...

    def findings(self, view: ScenarioView) -> list[Finding]: ...

    def candidates(self, view: ScenarioView) -> list[FigurantCandidate]: ...

    def status(self, view: ScenarioView, assessment: CoverageAssessment) -> Status:
        """From the facts alone: what a model wrote, or failed to write, never moves it."""
        ...
