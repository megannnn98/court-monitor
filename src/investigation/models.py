"""The contracts of an investigation: what is asked, what was found and where, what is
still missing. Every type is plain data and serializable, so the state of a run can be
stored or sent as it is.

A fact reaches a result only as a `Finding` that names the `EvidenceItem`s it rests on.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

SourceType = Literal["entity_group", "publication", "external"]
QueryKind = Literal["person", "publication_terms"]
Status = Literal["completed", "partial", "clarification_required", "review_required", "failed"]

DEFAULT_MAX_LLM_CALLS = 2
DEFAULT_MAX_FOLLOWUPS = 2
DEFAULT_MAX_EVIDENCE_ITEMS = 40


class InvestigationQuery(BaseModel):
    """One question to the retrievers."""

    model_config = ConfigDict(frozen=True)

    kind: QueryKind
    # The person asked about, as written.
    name: str
    # Set once the person is one entity group (ADR 0024).
    entity_key: str | None = None
    # `publication_terms`: the words to look for, and the fact they would tell.
    terms: tuple[str, ...] = ()
    fact: str | None = None


class EvidenceItem(BaseModel):
    """One thing found, with where it comes from. `text` is quoted, never rewritten."""

    model_config = ConfigDict(frozen=True)

    # Given when evidence is merged: «E1», «E2», … — what a finding cites.
    ref: str = ""
    source_type: SourceType
    # The retriever that found it.
    source_id: str
    url: str | None = None
    article_id: int | None = None
    entity_key: str | None = None
    span_start: int | None = None
    span_end: int | None = None
    text: str
    published_at: datetime | None = None
    confidence: float | None = None
    # A fact the pipeline already decided («role», «politics», …) and its value; None
    # for a quote that only shows what a publication says.
    fact: str | None = None
    value: str | None = None
    # The missing facts this quote was looked up for. A lead is not a fact: a person, or
    # a later step of the pipeline, decides what it tells.
    leads: tuple[str, ...] = ()

    @property
    def identity(self) -> tuple[object, ...]:
        """What makes two items the same piece of evidence."""
        if self.fact is None and self.article_id is not None:
            # A quote is its place in its article, whoever found it and for whom.
            return (self.source_type, self.article_id, self.span_start, self.span_end)
        return (self.source_type, self.entity_key, self.fact, self.value, self.text)


class SubjectCandidate(BaseModel):
    entity_key: str
    name: str
    # How much the database holds of them, in words: what a person choosing reads.
    description: str


class Subject(BaseModel):
    """Who the investigation is about. `entity_key` is set only when exactly one
    entity group answers the name."""

    name: str
    entity_key: str | None = None
    candidates: list[SubjectCandidate] = Field(default_factory=list)


class Finding(BaseModel):
    """A fact of the result. It cannot exist without evidence."""

    fact: str
    statement: str
    evidence: list[str] = Field(min_length=1)


class CoverageAssessment(BaseModel):
    enough: bool
    known: list[str] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    follow_up_queries: list[InvestigationQuery] = Field(default_factory=list)


class Usage(BaseModel):
    """What a run spent. Summed across the nodes of the graph."""

    llm_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    search_calls: int = 0
    followup_rounds: int = 0

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            llm_calls=self.llm_calls + other.llm_calls,
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cost_usd=self.cost_usd + other.cost_usd,
            search_calls=self.search_calls + other.search_calls,
            followup_rounds=self.followup_rounds + other.followup_rounds,
        )


def _limit(env: Mapping[str, str], name: str, default: int) -> int:
    raw = env.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from None
    if value < 0:
        raise ValueError(f"{name} must not be negative, got {value}")
    return value


class Limits(BaseModel):
    """Hard ceilings of one run. Reaching one ends that kind of work; it is said in the
    result's warnings, never worked around."""

    model_config = ConfigDict(frozen=True)

    max_llm_calls: int = Field(default=DEFAULT_MAX_LLM_CALLS, ge=0)
    max_followups: int = Field(default=DEFAULT_MAX_FOLLOWUPS, ge=0)
    max_evidence_items: int = Field(default=DEFAULT_MAX_EVIDENCE_ITEMS, ge=1)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Limits:
        env = os.environ if env is None else env
        return cls(
            max_llm_calls=_limit(env, "INVESTIGATION_MAX_LLM_CALLS", DEFAULT_MAX_LLM_CALLS),
            max_followups=_limit(env, "INVESTIGATION_MAX_FOLLOWUPS", DEFAULT_MAX_FOLLOWUPS),
            max_evidence_items=max(
                _limit(env, "INVESTIGATION_MAX_EVIDENCE_ITEMS", DEFAULT_MAX_EVIDENCE_ITEMS), 1
            ),
        )


class InvestigationResult(BaseModel):
    status: Status
    question: str
    subject: Subject | None = None
    findings: list[Finding] = Field(default_factory=list)
    evidence: list[EvidenceItem] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    # A model's wording of the evidence; None when no model was asked. Each sentence
    # cites evidence that exists, and that is all that is checked: whether the evidence
    # says what the sentence says is not. It adds no fact to `findings` and does not
    # move `status`; read it against the evidence it cites.
    model_summary: str | None = None
    usage: Usage = Field(default_factory=Usage)
