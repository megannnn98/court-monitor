"""The contracts of an investigation: what is asked, how it is planned, what was found
and where, what is still missing. Every type is plain data and serializable, so the
state of a run can be stored, resumed or sent as it is.

A fact reaches a result only as a `Finding` that names the `EvidenceItem`s it rests on.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Mapping
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator

# Where a piece of evidence comes from: the pipeline's verdicts on an entity group, a
# stored publication, an entry of the Rosfinmonitoring list, an unnamed figurant's
# description, or a source outside the database.
SourceType = Literal["entity_group", "publication", "rf_entry", "unnamed", "external"]
# What a query asks:
# - person: who answers this name;
# - publication_terms: where the person's own publications say one of these words;
# - keywords: ranked publications for free words (a lexical or a semantic index);
# - unnamed: an unnamed figurant of one publication, and who the list says they may be;
# - publication_people: the people of the database the given publications name.
QueryKind = Literal["person", "publication_terms", "keywords", "unnamed", "publication_people"]
QUERY_KINDS: tuple[QueryKind, ...] = (
    "person",
    "publication_terms",
    "keywords",
    "unnamed",
    "publication_people",
)
Status = Literal["completed", "partial", "clarification_required", "review_required", "failed"]
# A scenario is named by its registry, not by this module: the graph runs whatever
# scenarios it is given (`scenarios.registry.ScenarioRegistry`).
ScenarioName = str
# The scenarios Court Monitor ships: what a request from outside may name.
KnownScenario = Literal["person_dossier", "identify_unnamed"]
SCENARIOS: tuple[KnownScenario, ...] = ("person_dossier", "identify_unnamed")
ScoreType = Literal["exact_name", "declined_name", "ts_rank", "source_order"]
# What a score could be called while the dense retrieval existed (ADR 0028). A run stored
# then still holds them; read back, such a score keeps its number and loses its name.
REMOVED_SCORE_TYPES = frozenset({"cosine", "rrf"})

DEFAULT_MAX_LLM_CALLS = 2
DEFAULT_MAX_FOLLOWUPS = 2
DEFAULT_MAX_EVIDENCE_ITEMS = 40
DEFAULT_MAX_SEARCHES = 40
DEFAULT_MAX_PARALLEL_SEARCHES = 4
DEFAULT_TIMEOUT_SECONDS = 120
DEFAULT_SEARCH_TIMEOUT_SECONDS = 20
# A query's words: what a retriever is sent is never a whole text.
MAX_QUERY_LENGTH = 200


class InvestigationQuery(BaseModel):
    """One question to the retrievers."""

    model_config = ConfigDict(frozen=True)

    kind: QueryKind
    # The person asked about, as written; empty when nobody is named (`unnamed`).
    name: str = Field(default="", max_length=MAX_QUERY_LENGTH)
    # Set once the person is one entity group (ADR 0024).
    entity_key: str | None = None
    # `publication_terms`: the beginnings of words to look for. `keywords`: the words of
    # a ranked search, all of which should stand in a text.
    terms: tuple[str, ...] = ()
    # The fact the words would tell.
    fact: str | None = None
    # `unnamed`: the publication that describes the person.
    article_id: int | None = None
    # `publication_people`: the publications whose people are asked for.
    article_ids: tuple[int, ...] = ()
    # A window of publication dates, where a retriever can keep to one.
    published_from: datetime | None = None
    published_to: datetime | None = None

    @property
    def text(self) -> str:
        """The words a text search is sent: the name, then the terms."""
        return " ".join(part for part in (self.name, *self.terms) if part).strip()[
            :MAX_QUERY_LENGTH
        ]


class FactRequirement(BaseModel):
    """A fact a scenario expects of a result."""

    model_config = ConfigDict(frozen=True)

    key: str
    description: str
    required: bool = True


class RetrieverCapabilities(BaseModel):
    """What a retriever can be asked. The planner never sends a retriever a query it
    cannot answer, and never sends a network retriever anything in a local-only run."""

    model_config = ConfigDict(frozen=True)

    # The source a user names to keep to it (`--source local`, `--source telegram`).
    source: str
    local: bool
    network: bool = False
    kinds: frozenset[QueryKind]
    # The facts this retriever can bring evidence or leads for; empty: none beyond the
    # identity of whom it is asked about.
    facts: frozenset[str] = frozenset()
    supports_date_range: bool = False
    source_types: frozenset[SourceType] = frozenset()


class SearchTask(BaseModel):
    """One query to one retriever, and why it is asked."""

    model_config = ConfigDict(frozen=True)

    query: InvestigationQuery
    retriever: str
    # The fact it is looking for, or «identity» / «candidates».
    purpose: str
    # 0: the plan; 1…: the follow-up rounds.
    round: int = 0
    # Lower first, when a ceiling on searches cuts a round.
    priority: int = 0

    @computed_field  # type: ignore[prop-decorator]
    @property
    def fingerprint(self) -> str:
        """Same retriever, same query: the same search, whatever round or purpose."""
        payload = f"{self.retriever}|{self.query.model_dump_json()}"
        return hashlib.sha256(payload.encode()).hexdigest()[:24]


class PersonSubject(BaseModel):
    kind: Literal["person"] = "person"
    name: str


class ArticleSubject(BaseModel):
    kind: Literal["article"] = "article"
    article_id: int


SubjectRef = PersonSubject | ArticleSubject


class InvestigationIntent(BaseModel):
    """What the question asks, structured. Rules read it; a model may help, within the
    same schema and the same checks."""

    scenario: ScenarioName
    subject_name: str | None = None
    article_id: int | None = None
    # Fact keys of the scenario; empty: all of them.
    requested_facts: list[str] = Field(default_factory=list)
    # The sources the request keeps to (`--source`); empty: every allowed one.
    source_constraints: list[str] = Field(default_factory=list)
    # rules, or model when a model's reading passed the checks.
    read_by: Literal["rules", "model"] = "rules"


class InvestigationPlan(BaseModel):
    """What the run will look for first, and where. Holds no text of a publication."""

    scenario: ScenarioName
    subject: SubjectRef | None = None
    requirements: list[FactRequirement] = Field(default_factory=list)
    tasks: list[SearchTask] = Field(default_factory=list)


class EvidenceItem(BaseModel):
    """One thing found, with where it comes from. `text` is quoted, never rewritten."""

    model_config = ConfigDict(frozen=True)

    # Given when evidence is merged: «E1», «E2», … — what a finding cites.
    ref: str = ""
    source_type: SourceType
    # The retriever that found it.
    source_id: str
    # The source as people call it («ОВД-Инфо LIVE»), when known.
    source_name: str | None = None
    # The item's own id at its source (a post number), when it has one.
    external_id: str | None = None
    url: str | None = None
    article_id: int | None = None
    title: str | None = None
    entity_key: str | None = None
    span_start: int | None = None
    span_end: int | None = None
    text: str
    published_at: datetime | None = None
    retrieved_at: datetime | None = None
    confidence: float | None = None
    # The retriever's own score and what kind it is: two kinds never compare.
    score: float | None = None
    score_type: ScoreType | None = None

    @field_validator("score_type", mode="before")
    @classmethod
    def _without_a_removed_kind(cls, value: object) -> object:
        """A stored run's evidence may name a kind of score that no retriever gives any
        more; the evidence is still read, its score without a kind."""
        return None if value in REMOVED_SCORE_TYPES else value

    # The words searched for and the round of the search that found it.
    query: str | None = None
    round: int | None = None
    # A fact the pipeline already decided («role», «politics», …) and its value; None
    # for a quote that only shows what a publication says.
    fact: str | None = None
    value: str | None = None
    # The missing facts this quote was looked up for. A lead is not a fact: a person, or
    # a later step of the pipeline, decides what it tells.
    leads: tuple[str, ...] = ()

    @computed_field  # type: ignore[prop-decorator]
    @property
    def retriever(self) -> str:
        return self.source_id

    @property
    def identity(self) -> tuple[object, ...]:
        """What makes two items the same piece of evidence."""
        if self.fact is None and self.article_id is not None:
            # A quote is its place in its article, whoever found it and for whom.
            return (self.source_type, self.article_id, self.span_start, self.span_end)
        if self.fact is None and (self.url or self.external_id):
            # A post outside is the post, however many queries found it.
            return (self.source_type, self.source_name, self.external_id or canonical(self.url))
        return (self.source_type, self.entity_key, self.fact, self.value, self.text)

    @property
    def document(self) -> tuple[object, ...] | None:
        """The document it quotes, by the strongest id it has; None for a fact."""
        if self.fact is not None:
            return None
        if self.article_id is not None:
            return ("article", self.article_id)
        if self.url:
            return ("url", canonical(self.url))
        return ("text", fingerprint_text(self.text))


def canonical(url: str | None) -> str:
    """A URL as two sources write it alike: no scheme, no «www.», no query or fragment
    of the embed view, no trailing slash."""
    if not url:
        return ""
    value = url.split("#", 1)[0].split("?", 1)[0].lower()
    value = value.removeprefix("https://").removeprefix("http://").removeprefix("www.")
    return value.rstrip("/")


def fingerprint_text(text: str) -> str:
    folded = " ".join(text.lower().replace("ё", "е").split())
    return hashlib.sha256(folded.encode()).hexdigest()[:16]


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


class Conflict(BaseModel):
    """Two pieces of evidence that cannot both be right about one fact."""

    fact: str
    description: str
    # The refs on each side; empty when the conflict is between a name and the people
    # who answer it.
    evidence: list[str] = Field(default_factory=list)
    critical: bool = True


class CandidateSignal(BaseModel):
    """One reason a candidate fits an unnamed figurant, and what it weighs."""

    signal: str
    weight: float
    evidence: list[str] = Field(default_factory=list)


class FigurantCandidate(BaseModel):
    """Who an unnamed figurant may be: a list entry or a person of the database."""

    key: str
    name: str
    kind: Literal["rf_entry", "entity_group"]
    score: float
    signals: list[CandidateSignal] = Field(default_factory=list)


class CoverageAssessment(BaseModel):
    enough: bool
    known: list[str] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)
    # The conflicts as they are reported (`Conflict.description`).
    conflicts: list[str] = Field(default_factory=list)
    conflict_details: list[Conflict] = Field(default_factory=list)
    # The refs of quotes found for a missing fact, not yet a fact.
    leads: list[str] = Field(default_factory=list)
    # Of a scenario: the next round's queries, before they are given retrievers.
    follow_up_queries: list[InvestigationQuery] = Field(default_factory=list)
    # The next round's searches: only what is missing, only where it can be found,
    # nothing searched before.
    follow_up_tasks: list[SearchTask] = Field(default_factory=list)
    # Of `missing`, the facts no lead was found for; None: all of `missing`.
    open_gaps: list[str] | None = None
    # Facts still missing and no search left that could find them.
    exhausted: bool = False
    warnings: list[str] = Field(default_factory=list)


class Usage(BaseModel):
    """What a run spent. Summed across the nodes of the graph."""

    llm_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    search_calls: int = 0
    network_searches: int = 0
    local_searches: int = 0
    failed_searches: int = 0
    followup_rounds: int = 0
    evidence_items: int = 0
    elapsed_ms: int = 0

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            llm_calls=self.llm_calls + other.llm_calls,
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cost_usd=self.cost_usd + other.cost_usd,
            search_calls=self.search_calls + other.search_calls,
            network_searches=self.network_searches + other.network_searches,
            local_searches=self.local_searches + other.local_searches,
            failed_searches=self.failed_searches + other.failed_searches,
            followup_rounds=self.followup_rounds + other.followup_rounds,
            # Set once, by the report: the count of what the result holds.
            evidence_items=max(self.evidence_items, other.evidence_items),
            elapsed_ms=max(self.elapsed_ms, other.elapsed_ms),
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
    max_searches: int = Field(default=DEFAULT_MAX_SEARCHES, ge=1)
    max_parallel_searches: int = Field(default=DEFAULT_MAX_PARALLEL_SEARCHES, ge=1)
    # The whole run, and one search of it.
    timeout_seconds: int = Field(default=DEFAULT_TIMEOUT_SECONDS, ge=1)
    search_timeout_seconds: int = Field(default=DEFAULT_SEARCH_TIMEOUT_SECONDS, ge=1)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Limits:
        env = os.environ if env is None else env
        return cls(
            max_llm_calls=_limit(env, "INVESTIGATION_MAX_LLM_CALLS", DEFAULT_MAX_LLM_CALLS),
            max_followups=_limit(env, "INVESTIGATION_MAX_FOLLOWUPS", DEFAULT_MAX_FOLLOWUPS),
            max_evidence_items=max(
                _limit(env, "INVESTIGATION_MAX_EVIDENCE_ITEMS", DEFAULT_MAX_EVIDENCE_ITEMS), 1
            ),
            max_searches=max(_limit(env, "INVESTIGATION_MAX_SEARCHES", DEFAULT_MAX_SEARCHES), 1),
            max_parallel_searches=max(
                _limit(env, "INVESTIGATION_MAX_PARALLEL_SEARCHES", DEFAULT_MAX_PARALLEL_SEARCHES),
                1,
            ),
            timeout_seconds=max(
                _limit(env, "INVESTIGATION_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS), 1
            ),
            search_timeout_seconds=max(
                _limit(env, "INVESTIGATION_SEARCH_TIMEOUT_SECONDS", DEFAULT_SEARCH_TIMEOUT_SECONDS),
                1,
            ),
        )

    def narrowed(self, **requested: int | None) -> Limits:
        """These limits, each lowered to what a run asks; a request never raises one."""
        update: dict[str, int] = {}
        for name, value in requested.items():
            if value is None:
                continue
            if name not in Limits.model_fields:
                raise ValueError(f"unknown limit: {name}")
            floor = 1 if name != "max_llm_calls" and name != "max_followups" else 0
            update[name] = max(floor, min(value, getattr(self, name)))
        return self.model_copy(update=update)

    def within(self, ceilings: Limits) -> Limits:
        """These limits, none above `ceilings`: what a stored run asked for is held to
        what the service allows now."""
        return self.model_copy(
            update={
                name: min(getattr(self, name), getattr(ceilings, name))
                for name in Limits.model_fields
            }
        )


class InvestigationResult(BaseModel):
    status: Status
    question: str
    # Set when the run is stored: what `GET /api/v1/investigation/{run_id}` reads.
    run_id: str | None = None
    scenario: ScenarioName | None = None
    intent: InvestigationIntent | None = None
    plan: InvestigationPlan | None = None
    subject: Subject | None = None
    findings: list[Finding] = Field(default_factory=list)
    evidence: list[EvidenceItem] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    conflict_details: list[Conflict] = Field(default_factory=list)
    # `identify_unnamed`: who the figurant may be, the likeliest first, with why.
    candidates: list[FigurantCandidate] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    # A model's wording of the evidence; None when no model was asked. Each sentence
    # cites evidence that exists, and that is all that is checked: whether the evidence
    # says what the sentence says is not. It adds no fact to `findings` and does not
    # move `status`; read it against the evidence it cites.
    model_summary: str | None = None
    usage: Usage = Field(default_factory=Usage)
