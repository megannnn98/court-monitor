"""ER v2 evaluation: candidate recall per generator and decision-action metrics.

Accuracy is not reported on its own: the dangerous error is a false AUTO_LINK,
so false links, missed links (false CREATE_NEW) and unnecessary reviews are
counted separately. Runs against a disposable database (dry-run plans only).

`indistinguishable` cases are real namesakes the data cannot tell apart (one
existing person with the same full name, a different human incoming): any
system without context links them. They are reported on their own, not hidden
inside the false-link count of distinguishable cases.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from itertools import product
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from db.orm_models import PersonAliasRecord, PersonRecord
from extraction.normalizers import RuleBasedMentionNormalizer
from persons.persistence import SqlAlchemyPersonPersistence
from persons.resolution.candidates import (
    CandidateConfig,
    CompositeCandidateGenerator,
    ExactKeyCandidateGenerator,
    PersonCandidateGenerator,
    SemanticCandidateGenerator,
    TrigramCandidateGenerator,
)
from persons.resolution.decision import PersonResolutionDecisionPolicy, ResolutionThresholds
from persons.resolution.models import (
    PersonIdentityInput,
    PersonResolutionAction,
    ScoredPersonCandidate,
)
from persons.resolution.service import PersonResolutionEngine
from semantic_retrieval.evaluation import CorpusPerson, EntityRetrievalCorpus, seed_corpus
from semantic_retrieval.retrievers import EntityRetriever

A = PersonResolutionAction
RECALL_KS = (1, 5, 10)


class ErCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    incoming: str
    category: Literal["positive", "negative", "ambiguous", "indistinguishable"]
    tags: list[str] = Field(default_factory=list)
    expected_action: PersonResolutionAction
    true_person: str | None

    @model_validator(mode="after")
    def validate_truth(self) -> ErCase:
        if self.category == "positive" and self.true_person is None:
            raise ValueError(f"{self.id}: a positive case needs true_person")
        if self.category != "positive" and self.true_person is not None:
            raise ValueError(f"{self.id}: only positive cases have a true person")
        return self


class ErCorpus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    persons: list[CorpusPerson]
    cases: list[ErCase]

    @model_validator(mode="after")
    def validate_references(self) -> ErCorpus:
        keys = {person.key for person in self.persons}
        for case in self.cases:
            if case.true_person is not None and case.true_person not in keys:
                raise ValueError(f"{case.id}: unknown true_person {case.true_person}")
        if len({case.id for case in self.cases}) != len(self.cases):
            raise ValueError("case ids must be unique")
        return self


def load_er_corpus(path: Path) -> ErCorpus:
    return ErCorpus.model_validate_json(path.read_text(encoding="utf-8"))


_extraction_normalizer = RuleBasedMentionNormalizer()


def pipeline_identity(surface: str) -> PersonIdentityInput:
    """The identity the extraction pipeline would hand to ER for this surface form."""
    _, data = _extraction_normalizer.normalize_person(surface)
    return PersonIdentityInput(
        name=data.full_name, surface_text=surface, matching_key=data.matching_key
    )


def seed_er_corpus(session_factory: sessionmaker[Session], corpus: ErCorpus) -> dict[str, int]:
    """Seed persons (with events for semantic documents) as the pipeline stores them."""
    ids = seed_corpus(session_factory, EntityRetrievalCorpus(persons=corpus.persons)).persons
    with session_factory.begin() as session:
        for person in corpus.persons:
            stored = pipeline_identity(person.canonical_name)
            session.execute(
                update(PersonRecord)
                .where(PersonRecord.id == ids[person.key])
                .values(normalized_name=stored.name, matching_key=stored.matching_key)
            )
            for alias in session.scalars(
                select(PersonAliasRecord).where(PersonAliasRecord.person_id == ids[person.key])
            ).all():
                alias_identity = pipeline_identity(alias.surface_text)
                alias.normalized_text = alias_identity.name
                alias.matching_key = alias_identity.matching_key or ""
    return ids


class CaseOutcome(BaseModel):
    case_id: str
    category: str
    tags: list[str]
    expected_action: PersonResolutionAction
    true_person: str | None
    action: PersonResolutionAction
    selected_person: str | None
    candidates: list[str]
    reasons: list[str]
    top_score: float | None
    margin: float | None
    # Kept to re-decide under other thresholds without touching the database.
    scored: list[ScoredPersonCandidate] = Field(default_factory=list, exclude=True)


class DecisionMetrics(BaseModel):
    # Distinguishable cases; indistinguishable namesakes are counted apart.
    cases: int
    indistinguishable_cases: int
    cases_with_true_person: int
    auto_links: int
    correct_auto_links: int
    # AUTO_LINK to a wrong person or when the true person does not exist, over
    # distinguishable cases only.
    false_links: int
    # AUTO_LINK of an indistinguishable namesake: a known limitation, not a policy error.
    indistinguishable_namesake_links: int
    false_link_rate: float
    auto_link_precision: float | None
    auto_link_recall: float
    reviews: int
    review_rate: float
    unnecessary_reviews: int
    false_create_new: int
    false_create_new_rate: float
    missed_reviews: int
    actions: dict[str, dict[str, int]]


def decision_metrics(outcomes: Sequence[CaseOutcome]) -> DecisionMetrics:
    indistinguishable = [o for o in outcomes if o.category == "indistinguishable"]
    namesake_links = [o for o in indistinguishable if o.action is A.AUTO_LINK]
    outcomes = [o for o in outcomes if o.category != "indistinguishable"]
    with_truth = [o for o in outcomes if o.true_person is not None]
    auto = [o for o in outcomes if o.action is A.AUTO_LINK]
    correct = [o for o in auto if o.selected_person == o.true_person]
    reviews = [o for o in outcomes if o.action is A.REVIEW]
    false_create = [o for o in with_truth if o.action is A.CREATE_NEW]
    actions: dict[str, dict[str, int]] = {}
    for outcome in outcomes:
        row = actions.setdefault(outcome.expected_action.value, {a.value: 0 for a in A})
        row[outcome.action.value] += 1
    total = len(outcomes) or 1
    return DecisionMetrics(
        cases=len(outcomes),
        indistinguishable_cases=len(indistinguishable),
        cases_with_true_person=len(with_truth),
        auto_links=len(auto),
        correct_auto_links=len(correct),
        false_links=len(auto) - len(correct),
        indistinguishable_namesake_links=len(namesake_links),
        false_link_rate=round((len(auto) - len(correct)) / total, 4),
        auto_link_precision=round(len(correct) / len(auto), 4) if auto else None,
        auto_link_recall=round(len(correct) / (len(with_truth) or 1), 4),
        reviews=len(reviews),
        review_rate=round(len(reviews) / total, 4),
        unnecessary_reviews=sum(1 for o in reviews if o.expected_action is not A.REVIEW),
        false_create_new=len(false_create),
        false_create_new_rate=round(len(false_create) / (len(with_truth) or 1), 4),
        # Expected a human look, got an automatic action instead.
        missed_reviews=sum(
            1 for o in outcomes if o.expected_action is A.REVIEW and o.action is not A.REVIEW
        ),
        actions=actions,
    )


# --- runs ------------------------------------------------------------------


GeneratorSet = Literal["exact", "trigram", "semantic", "combined"]


class GeneratorRecall(BaseModel):
    generator: GeneratorSet
    cases_with_true_person: int
    recall_at: dict[int, float]
    mean_candidates: float


class ThresholdRow(BaseModel):
    auto_link_min_score: float
    review_min_score: float
    min_margin: float
    metrics: DecisionMetrics


class ErEvaluationRun(BaseModel):
    thresholds: ResolutionThresholds
    candidate_limit: int
    semantic_enabled: bool
    decision: DecisionMetrics
    generators: list[GeneratorRecall]
    outcomes: list[CaseOutcome]
    sweep: list[ThresholdRow] = Field(default_factory=list)

    model_config = ConfigDict(arbitrary_types_allowed=True)


def _engine(
    session_factory: sessionmaker[Session],
    generators: Sequence[PersonCandidateGenerator],
    policy: PersonResolutionDecisionPolicy,
    config: CandidateConfig,
) -> PersonResolutionEngine:
    return PersonResolutionEngine(
        persistence=SqlAlchemyPersonPersistence(session_factory),
        generator=CompositeCandidateGenerator(generators),
        policy=policy,
        config=config,
    )


def evaluate_cases(
    session_factory: sessionmaker[Session],
    cases: Sequence[ErCase],
    ids: Mapping[str, int],
    *,
    generators: Sequence[PersonCandidateGenerator],
    policy: PersonResolutionDecisionPolicy,
    config: CandidateConfig,
) -> list[CaseOutcome]:
    keys = {person_id: key for key, person_id in ids.items()}
    engine = _engine(session_factory, generators, policy, config)
    outcomes = []
    with session_factory() as session:
        for case in cases:
            plan = engine.plan(pipeline_identity(case.incoming), session)
            decision = plan.decision
            outcomes.append(
                CaseOutcome(
                    case_id=case.id,
                    category=case.category,
                    tags=case.tags,
                    expected_action=case.expected_action,
                    true_person=case.true_person,
                    action=decision.action,
                    selected_person=keys.get(decision.selected_person_id or -1),
                    candidates=[
                        keys.get(c.person_id, str(c.person_id)) for c in decision.candidates
                    ],
                    reasons=[reason.value for reason in decision.reasons],
                    top_score=decision.candidates[0].resolution_score
                    if decision.candidates
                    else None,
                    margin=decision.decision_margin,
                    scored=decision.candidates,
                )
            )
        session.rollback()
    return outcomes


def generator_recall(
    session_factory: sessionmaker[Session],
    cases: Sequence[ErCase],
    ids: Mapping[str, int],
    *,
    name: GeneratorSet,
    generators: Sequence[PersonCandidateGenerator],
    limit: int,
) -> GeneratorRecall:
    """Is the true person among the first k candidates of this generator set?

    Exact covers person and known-alias `matching_key` lookups.
    """
    composite = CompositeCandidateGenerator(generators)
    with_truth = [case for case in cases if case.true_person is not None]
    hits = dict.fromkeys(RECALL_KS, 0)
    total_candidates = 0
    with session_factory() as session:
        for case in with_truth:
            identity = pipeline_identity(case.incoming)
            ranked = [
                candidate.person_id
                for candidate in composite.generate(
                    identity, limit=limit, session=session
                ).candidates
            ]
            total_candidates += len(ranked)
            truth = ids[case.true_person or ""]
            for k in RECALL_KS:
                hits[k] += truth in ranked[:k]
        session.rollback()
    count = len(with_truth) or 1
    return GeneratorRecall(
        generator=name,
        cases_with_true_person=len(with_truth),
        recall_at={k: round(hits[k] / count, 4) for k in RECALL_KS},
        mean_candidates=round(total_candidates / count, 2),
    )


def sweep_thresholds(
    cases: Sequence[ErCase],
    outcomes: Sequence[CaseOutcome],
    keys: Mapping[int, str],
    grid: Sequence[ResolutionThresholds],
) -> list[ThresholdRow]:
    """Re-decide stored scored candidates under other thresholds (no database)."""
    by_id = {case.id: case for case in cases}
    rows = []
    for thresholds in grid:
        policy = PersonResolutionDecisionPolicy(thresholds)
        redecided = []
        for outcome in outcomes:
            case = by_id[outcome.case_id]
            decision = policy.decide(pipeline_identity(case.incoming), outcome.scored)
            redecided.append(
                outcome.model_copy(
                    update={
                        "action": decision.action,
                        "selected_person": keys.get(decision.selected_person_id or -1),
                    }
                )
            )
        rows.append(
            ThresholdRow(
                auto_link_min_score=thresholds.auto_link_min_score,
                review_min_score=thresholds.review_min_score,
                min_margin=thresholds.min_margin,
                metrics=decision_metrics(redecided),
            )
        )
    return rows


def default_grid() -> list[ResolutionThresholds]:
    grid = []
    for auto, review, margin in product(
        (0.7, 0.75, 0.8, 0.85, 0.9, 0.95, 1.0), (0.4, 0.5, 0.6), (0.05, 0.1, 0.2)
    ):
        if review <= auto:
            grid.append(
                ResolutionThresholds(
                    auto_link_min_score=auto, review_min_score=review, min_margin=margin
                )
            )
    return grid


def run_er_evaluation(
    session_factory: sessionmaker[Session],
    corpus: ErCorpus,
    *,
    thresholds: ResolutionThresholds | None = None,
    config: CandidateConfig | None = None,
    semantic_retriever: EntityRetriever | None = None,
    index_semantic: Callable[[], None] | None = None,
    sweep: bool = False,
) -> ErEvaluationRun:
    """Seed the corpus into an empty disposable database and evaluate ER v2."""
    thresholds = thresholds or ResolutionThresholds()
    config = config or CandidateConfig()
    ids = seed_er_corpus(session_factory, corpus)
    if index_semantic is not None:
        index_semantic()
    semantic: list[PersonCandidateGenerator] = (
        [SemanticCandidateGenerator(semantic_retriever, min_score=config.semantic_min_score)]
        if semantic_retriever is not None
        else []
    )
    lexical: list[PersonCandidateGenerator] = [
        ExactKeyCandidateGenerator(),
        TrigramCandidateGenerator(),
    ]
    use_semantic = config.semantic_enabled and bool(semantic)
    generators = lexical + (semantic if use_semantic else [])
    outcomes = evaluate_cases(
        session_factory,
        corpus.cases,
        ids,
        generators=generators,
        policy=PersonResolutionDecisionPolicy(thresholds),
        config=config,
    )
    limit = config.candidate_limit
    recall = [
        generator_recall(
            session_factory,
            corpus.cases,
            ids,
            name="exact",
            generators=[ExactKeyCandidateGenerator()],
            limit=limit,
        ),
        generator_recall(
            session_factory,
            corpus.cases,
            ids,
            name="trigram",
            generators=[TrigramCandidateGenerator()],
            limit=limit,
        ),
    ]
    if semantic:
        recall.append(
            generator_recall(
                session_factory,
                corpus.cases,
                ids,
                name="semantic",
                generators=semantic,
                limit=limit,
            )
        )
    recall.append(
        generator_recall(
            session_factory,
            corpus.cases,
            ids,
            name="combined",
            generators=lexical + semantic,
            limit=limit,
        )
    )
    keys = {person_id: key for key, person_id in ids.items()}
    return ErEvaluationRun(
        thresholds=thresholds,
        candidate_limit=limit,
        semantic_enabled=use_semantic,
        decision=decision_metrics(outcomes),
        generators=recall,
        outcomes=outcomes,
        sweep=sweep_thresholds(corpus.cases, outcomes, keys, default_grid()) if sweep else [],
    )


def format_er_evaluation(run: ErEvaluationRun) -> str:
    d = run.decision
    t = run.thresholds
    lines = [
        "## Candidate generation (recall@k over cases with a true person)",
        "",
        "| generator | " + " | ".join(f"recall@{k}" for k in RECALL_KS) + " | mean candidates |",
        "|---|" + "---|" * (len(RECALL_KS) + 1),
    ]
    for row in run.generators:
        lines.append(
            f"| {row.generator} | "
            + " | ".join(f"{row.recall_at[k]:.2f}" for k in RECALL_KS)
            + f" | {row.mean_candidates:.1f} |"
        )
    precision = "n/a" if d.auto_link_precision is None else f"{d.auto_link_precision:.2f}"
    lines += [
        "",
        (
            f"## Decisions (auto-link >= {t.auto_link_min_score}, review >= {t.review_min_score}, "
            f"margin > {t.min_margin}, semantic {'on' if run.semantic_enabled else 'off'})"
        ),
        "",
        (
            f"cases: {d.cases} (with a true person: {d.cases_with_true_person}; "
            f"+{d.indistinguishable_cases} indistinguishable, reported apart; "
            "rates are over distinguishable cases)"
        ),
        f"auto-link precision: {precision}   auto-link recall: {d.auto_link_recall:.2f}",
        f"false links: {d.false_links} (rate {d.false_link_rate:.2f})",
        (
            "indistinguishable namesake links (known limitation, not counted above): "
            f"{d.indistinguishable_namesake_links}"
        ),
        f"false create-new (missed links): {d.false_create_new} (rate {d.false_create_new_rate:.2f})",
        (
            f"review rate: {d.review_rate:.2f}   unnecessary reviews: {d.unnecessary_reviews}   "
            f"missed reviews: {d.missed_reviews}"
        ),
        "",
        "| expected \\ actual | " + " | ".join(a.value for a in A) + " |",
        "|---|" + "---|" * len(A),
    ]
    for expected, counts in sorted(d.actions.items()):
        lines.append(f"| {expected} | " + " | ".join(str(counts[a.value]) for a in A) + " |")
    wrong = [o for o in run.outcomes if o.action is not o.expected_action]
    if wrong:
        lines += ["", "Cases with an action different from the label:"]
        for o in wrong:
            lines.append(
                f"- {o.case_id}: expected {o.expected_action.value}, got {o.action.value}"
                f" (top {o.top_score}, reasons {', '.join(o.reasons)})"
            )
    if run.sweep:
        lines += [
            "",
            "## Threshold sweep",
            "",
            "| auto | review | margin | false links | auto precision | auto recall | review rate | false create-new |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for sweep_row in run.sweep:
            m = sweep_row.metrics
            p = "n/a" if m.auto_link_precision is None else f"{m.auto_link_precision:.2f}"
            lines.append(
                f"| {sweep_row.auto_link_min_score} | {sweep_row.review_min_score} | {sweep_row.min_margin} | "
                f"{m.false_links} | {p} | {m.auto_link_recall:.2f} | {m.review_rate:.2f} | {m.false_create_new} |"
            )
    return "\n".join(lines)
