"""`evaluate-real-world`: corpus run, component metrics, benchmarks, E2E scenarios, gates.

Order (one disposable database):

    namesake benchmark (existing ER evaluation on its own seeded persons)
    → temporal corpus run T0..T3 (with repeated runs in --full)
    → component metrics against the selected golden split
    → retrieval benchmark → research benchmark
    → --full: DB invariants, workload, performance, manual review continuation,
      then failure-injection scenarios on fresh databases
    → safety gates → JSON + Markdown report
"""

from __future__ import annotations

import logging
import os
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from db.maintenance import truncate_disposable_tables
from evaluation.final.report import code_commit, component_versions
from evaluation.real_world.component_evaluation import (
    IdentityMap,
    build_identity_map,
    evaluate_candidates,
    evaluate_entity_resolution,
    evaluate_extraction,
    evaluate_persecution,
    evaluate_rosfinmonitoring,
)
from evaluation.real_world.corpus_cache import RawCorpusCache
from evaluation.real_world.corpus_run import CorpusRunner, RfSnapshotFile, articles_by_period
from evaluation.real_world.db_state import load_pipeline_state
from evaluation.real_world.golden import (
    AnnotationStatus,
    GoldenDataset,
    GoldenSplit,
    manifest_problems,
    text_problems,
)
from evaluation.real_world.models import (
    EVALUATION_VERSION,
    REPOSITORY_ROOT,
    CorpusManifest,
    TemporalPeriod,
)
from evaluation.real_world.monitoring_simulation import (
    TokenHashEmbedder,
    crash_recovery,
    finding_periods,
    manual_review_continuation,
    no_rf_snapshot,
    performance,
    postgres_interruption,
    qdrant_outage,
    review_workload,
    rf_review_findings,
    run_temporal_simulation,
    semantic_indexer_factory,
    source_failures,
    together_failure,
)
from evaluation.real_world.namesake import NamesakeCorpus, evaluate_namesakes
from evaluation.real_world.policy import EvaluationPolicy
from evaluation.real_world.report import failure_summary, recommended_next_work
from evaluation.real_world.research_eval import ResearchQueryCase, evaluate_research
from evaluation.real_world.results import (
    DatasetSummary,
    Failure,
    MonitoringSection,
    PerformanceSection,
    Provenance,
    RealWorldValidationReport,
    RetrievalSection,
    SectionStatus,
    SplitCounts,
)
from evaluation.real_world.retrieval_eval import RealRetrievalQuery, evaluate_retrieval
from evaluation.real_world.safety import (
    GateInputs,
    RealWorldSafetyGateEvaluator,
    overall_status,
    workload_total,
)
from evaluation.real_world.state_snapshot import check_invariants, table_counts
from research.workflow.intake import ResearchRequestParser
from semantic_retrieval.document_store import PostgresLexicalEntityRetriever
from semantic_retrieval.factory import SemanticComponents, create_vector_store
from semantic_retrieval.indexer import SemanticIndexer
from semantic_retrieval.models import RetrievalBackend, RetrievalEntityType
from semantic_retrieval.retrievers import EntityRetriever

logger = logging.getLogger("evaluation.real_world")

REAL_WORLD_COLLECTIONS = {
    RetrievalEntityType.PERSON: "eval_real_world_persons",
    RetrievalEntityType.EVENT: "eval_real_world_events",
}
IN_MEMORY_QDRANT = ":memory:"

STATIC_LIMITATIONS = [
    "Golden annotations are DRAFT until a human verifies them; DRAFT metrics are preliminary.",
    (
        "The Rosfinmonitoring snapshot is a committed evaluation snapshot built for this corpus "
        "(listed names, name variants, ambiguous namesakes), not the real published list."
    ),
    (
        "Entity resolution metrics only see annotated articles: links into persons from "
        "non-annotated articles are evaluated only through the golden person's majority person."
    ),
    "The persecution classifier and RF matcher are rule-based; birth dates are not extracted.",
    (
        "Failure-injection scenarios inject controlled exceptions in-process; a killed process "
        "(SIGKILL) and a real PostgreSQL restart are not simulated."
    ),
]


@dataclass
class EvaluationOptions:
    split: GoldenSplit | None
    verified_only: bool
    full: bool
    semantic_model: bool
    qdrant_url: str | None
    llm_parser: ResearchRequestParser | None = None
    llm_not_run_reason: str | None = None


@dataclass
class EvaluationInputs:
    manifest: CorpusManifest
    manifest_path: Path
    cache: RawCorpusCache
    golden: GoldenDataset
    policy: EvaluationPolicy
    policy_hash: str
    rf_snapshot: RfSnapshotFile
    namesakes: NamesakeCorpus | None
    retrieval_queries: list[RealRetrievalQuery] = field(default_factory=list)
    research_queries: list[ResearchQueryCase] = field(default_factory=list)


class EvaluationDataError(ValueError):
    """Golden dataset, manifest or cache are inconsistent: exit code 2."""


def validate_inputs(inputs: EvaluationInputs, texts: dict[str, str]) -> None:
    problems = manifest_problems(inputs.golden, inputs.manifest) + text_problems(
        inputs.golden, texts
    )
    if inputs.golden.content_hash() != inputs.golden.version.golden_dataset_hash:
        problems.append(
            "golden_dataset_hash in VERSION.json does not match the annotations "
            "(run real-world-golden validate --write-hash and bump dataset_version)"
        )
    if problems:
        raise EvaluationDataError("\n".join(problems))


@dataclass
class SemanticSetup:
    create_indexer: Callable[[], SemanticIndexer]
    retrievers: Callable[[], dict[RetrievalBackend, EntityRetriever]]
    embedding_model_id: str | None
    not_run_reason: str | None
    available: bool


def semantic_setup(
    session_factory: sessionmaker[Session], options: EvaluationOptions
) -> SemanticSetup:
    lexical = PostgresLexicalEntityRetriever(session_factory)
    if not options.semantic_model:
        # Documents (and the lexical backend) still work with a token-hash index in memory.
        return SemanticSetup(
            create_indexer=semantic_indexer_factory(
                session_factory, IN_MEMORY_QDRANT, REAL_WORLD_COLLECTIONS
            ),
            retrievers=lambda: {RetrievalBackend.LEXICAL: lexical},
            embedding_model_id=None,
            not_run_reason="real embedding model not requested (--semantic-model)",
            available=False,
        )
    if options.qdrant_url is None:
        return SemanticSetup(
            create_indexer=semantic_indexer_factory(
                session_factory, IN_MEMORY_QDRANT, REAL_WORLD_COLLECTIONS
            ),
            retrievers=lambda: {RetrievalBackend.LEXICAL: lexical},
            embedding_model_id=None,
            not_run_reason="no Qdrant URL (--qdrant-url or QDRANT_TEST_URL)",
            available=False,
        )
    try:
        import sentence_transformers  # noqa: F401
    except ImportError:
        return SemanticSetup(
            create_indexer=semantic_indexer_factory(
                session_factory, IN_MEMORY_QDRANT, REAL_WORLD_COLLECTIONS
            ),
            retrievers=lambda: {RetrievalBackend.LEXICAL: lexical},
            embedding_model_id=None,
            not_run_reason="sentence-transformers not installed (uv sync --group semantic)",
            available=False,
        )
    from semantic_retrieval.embeddings import EmbeddingConfig, SentenceTransformerEmbedder

    embedder = SentenceTransformerEmbedder(EmbeddingConfig.from_env())
    store = create_vector_store(options.qdrant_url)
    components = SemanticComponents(
        session_factory=session_factory,
        store=store,
        embedder=embedder,
        collections=REAL_WORLD_COLLECTIONS,
    )
    for collection in REAL_WORLD_COLLECTIONS.values():
        store.recreate_collection(collection, embedder.dimension)
    return SemanticSetup(
        create_indexer=components.indexer,
        retrievers=lambda: {
            RetrievalBackend.LEXICAL: lexical,
            RetrievalBackend.DENSE: components.retriever(RetrievalBackend.DENSE),
            RetrievalBackend.HYBRID: components.retriever(RetrievalBackend.HYBRID),
        },
        embedding_model_id=embedder.model_id,
        not_run_reason=None,
        available=True,
    )


def semantic_index_problem(counts: Mapping[str, int]) -> str | None:
    """Why semantic retrieval cannot be measured on this run, if it cannot."""
    documents, indexed = counts.get("semantic_documents", 0), counts.get("semantic_indexed", 0)
    if documents == 0:
        return "semantic index is empty"
    if indexed < documents:
        return (
            f"semantic index incomplete: {indexed}/{documents} entities indexed "
            "(see monitoring_run_items)"
        )
    return None


def dataset_summary(inputs: EvaluationInputs, selected: GoldenDataset) -> DatasetSummary:
    manifest = inputs.manifest
    golden = inputs.golden
    by_split: dict[str, SplitCounts] = {}
    for split in GoldenSplit:
        articles = [a for a in golden.articles if a.split is split]
        by_split[split.value] = SplitCounts(
            draft=sum(a.annotation_status is AnnotationStatus.DRAFT for a in articles),
            verified=sum(a.annotation_status is AnnotationStatus.VERIFIED for a in articles),
        )
    published = sorted(a.published_at for a in manifest.articles)
    return DatasetSummary(
        corpus_articles=len(manifest.articles),
        corpus_by_source=dict(sorted(Counter(a.source for a in manifest.articles).items())),
        corpus_by_period=dict(
            sorted(Counter(a.corpus_split.value for a in manifest.articles).items())
        ),
        corpus_period=f"{manifest.period_start} .. {manifest.period_end}",
        corpus_published_range=f"{published[0].date()} .. {published[-1].date()}"
        if published
        else None,
        evaluation_sample=sum(a.evaluation_sample for a in manifest.articles),
        source_status={r.source: r.status.value for r in manifest.sources},
        golden_articles=len(golden.articles),
        golden_draft=sum(a.annotation_status is AnnotationStatus.DRAFT for a in golden.articles),
        golden_verified=sum(
            a.annotation_status is AnnotationStatus.VERIFIED for a in golden.articles
        ),
        golden_persons=len(golden.persons),
        golden_by_split=by_split,
        evaluated_articles=len(selected.articles),
        evaluated_persons=len(selected.persons),
        namesake_cases=len(inputs.namesakes.cases) if inputs.namesakes else 0,
        retrieval_queries=len(inputs.retrieval_queries),
        research_queries=len(inputs.research_queries),
    )


def _in_split(split: GoldenSplit | None, value: GoldenSplit) -> bool:
    return split is None or value is split


def run_evaluation(
    engine: Engine, inputs: EvaluationInputs, options: EvaluationOptions
) -> RealWorldValidationReport:
    started_at = datetime.now(UTC)
    failures: list[Failure] = []
    golden = inputs.golden
    verified_only = options.verified_only or options.split is GoldenSplit.TEST
    selected = golden.select(options.split, verified_only=verified_only)
    # Identity safety (false links) is checked over every usable annotation, not only the split.
    identity_scope = golden.select(None, verified_only=verified_only)

    # 1. Namesake benchmark (existing ER evaluation, own seeded persons).
    truncate_disposable_tables(engine)
    namesake = evaluate_namesakes(engine, inputs.namesakes)

    # 2. Corpus run.
    runner = CorpusRunner(engine=engine, manifest=inputs.manifest, cache=inputs.cache)
    semantic = semantic_setup(runner.session_factory, options)
    runner.create_semantic_indexer = semantic.create_indexer
    runner.rebuild_service()
    temporal = run_temporal_simulation(runner, inputs.rf_snapshot, rerun=options.full)
    snapshot_id = runner.snapshot_id
    session_factory = runner.session_factory

    with session_factory() as session:
        state = load_pipeline_state(session, snapshot_id=snapshot_id)
    identity = build_identity_map(identity_scope, state)

    # 3. Components on the selected split.
    split_failures: list[Failure] = []
    extraction = evaluate_extraction(selected, state, identity, split_failures)
    entity_resolution = evaluate_entity_resolution(selected, identity, split_failures)
    entity_resolution.namesake = namesake
    persecution = evaluate_persecution(selected, state, identity, split_failures)
    rosfin = evaluate_rosfinmonitoring(selected, state, identity, snapshot_id, split_failures)
    candidates = evaluate_candidates(
        selected, state, identity, session_factory, snapshot_id, split_failures
    )
    failures.extend(split_failures)
    # False links between persons of different splits are still safety errors.
    split_link_keys = {(f.golden_person_id, f.kind) for f in split_failures}
    scope_failures: list[Failure] = []
    evaluate_entity_resolution(identity_scope, identity, scope_failures)
    failures.extend(
        f
        for f in scope_failures
        if f.kind == "false_person_link" and (f.golden_person_id, f.kind) not in split_link_keys
    )

    retrieval_queries = [q for q in inputs.retrieval_queries if _in_split(options.split, q.split)]
    retrievers = semantic.retrievers()
    retrieval_not_run = semantic.not_run_reason
    index_problem = semantic_index_problem(table_counts(engine)) if semantic.available else None
    if index_problem is not None:
        # Infrastructure, not retrieval quality: no ranking over a missing index.
        retrievers = {}
        retrieval_not_run = index_problem
    retrieval: RetrievalSection = evaluate_retrieval(
        queries=retrieval_queries,
        dataset=identity_scope,
        state=state,
        identity=identity,
        retrievers=retrievers,
        embedding_model_id=semantic.embedding_model_id,
        not_run_reason=retrieval_not_run,
        failures=failures,
    )
    research_queries = [q for q in inputs.research_queries if _in_split(options.split, q.split)]
    research = evaluate_research(
        cases=research_queries,
        dataset=identity_scope,
        state=state,
        identity=identity,
        session_factory=session_factory,
        snapshot_id=snapshot_id,
        failures=failures,
        llm_parser=options.llm_parser,
        llm_not_run_reason=options.llm_not_run_reason,
        semantic_available=semantic.available,
    )

    # 4. Monitoring E2E.
    article_count = len(inputs.manifest.articles)
    monitoring = MonitoringSection(
        status=SectionStatus.RUN if options.full else SectionStatus.PARTIAL,
        articles=article_count,
        periods=temporal.periods,
        rerun_duplicates=temporal.rerun_duplicates if options.full else {},
        not_run_reason=None if options.full else "repeated runs and failure injection need --full",
    )
    monitoring.finding_timing = finding_timing(
        identity_scope, identity, session_factory, temporal.run_period, inputs.manifest
    )
    monitoring.db_invariants = check_invariants(engine)
    monitoring.review_workload = review_workload(engine, article_count)
    perf: PerformanceSection = performance(engine, temporal, article_count)
    monitoring.scenarios.append(rf_review_findings(session_factory, snapshot_id))
    if options.full:
        monitoring.scenarios.append(manual_review_continuation(runner, has_semantic=True))
        monitoring.scenarios.append(together_failure(runner))
        periods = articles_by_period(inputs.manifest)
        baseline = periods[TemporalPeriod.T2]
        increment = periods[TemporalPeriod.T3]
        monitoring.scenarios.extend(
            crash_recovery(
                runner,
                inputs.rf_snapshot,
                baseline,
                increment,
                semantic=lambda: semantic_indexer_factory(
                    session_factory, IN_MEMORY_QDRANT, REAL_WORLD_COLLECTIONS
                ),
            )
        )
        restore_url = options.qdrant_url or IN_MEMORY_QDRANT
        monitoring.scenarios.append(
            qdrant_outage(
                runner,
                inputs.rf_snapshot,
                increment,
                semantic_indexer_factory(
                    session_factory,
                    restore_url,
                    {k: f"{v}_outage" for k, v in REAL_WORLD_COLLECTIONS.items()},
                    TokenHashEmbedder(),
                ),
                REAL_WORLD_COLLECTIONS,
            )
        )
        monitoring.scenarios.append(source_failures(runner, inputs.rf_snapshot, increment))
        monitoring.scenarios.append(postgres_interruption(runner, inputs.rf_snapshot, increment))
        monitoring.scenarios.append(no_rf_snapshot(runner))

    gates = RealWorldSafetyGateEvaluator(inputs.policy).evaluate(
        GateInputs(
            extraction=extraction,
            entity_resolution=entity_resolution,
            persecution=persecution,
            candidate_query=candidates,
            retrieval=retrieval,
            research=research,
            monitoring=monitoring,
            failures=failures,
            review_workload_per_100=workload_total(monitoring.review_workload),
        )
    )
    verified = sum(a.annotation_status is AnnotationStatus.VERIFIED for a in selected.articles)
    drafts = sum(a.annotation_status is AnnotationStatus.DRAFT for a in selected.articles)
    status, exit_code, reasons = overall_status(
        gates,
        verified_articles=verified,
        draft_articles_evaluated=drafts,
        min_verified_articles=inputs.policy.min_verified_articles,
    )
    versions = component_versions()
    limitations = list(STATIC_LIMITATIONS)
    total_target = inputs.manifest.total_target
    if article_count < total_target:
        limitations.insert(
            0,
            f"Corpus size {article_count} < target {total_target}: the existing adapters list only "
            + ", ".join(f"{r.source}={r.in_period}" for r in inputs.manifest.sources)
            + f" publications between {inputs.manifest.period_start} and {inputs.manifest.period_end}.",
        )
    limitations += sorted({a.known_limitation for a in selected.articles if a.known_limitation})
    report = RealWorldValidationReport(
        evaluation_version=EVALUATION_VERSION,
        overall_status=status,
        exit_code=exit_code,
        status_reasons=reasons,
        provenance=Provenance(
            git_commit=code_commit(),
            evaluation_version=EVALUATION_VERSION,
            dataset_version=golden.version.dataset_version,
            corpus_manifest_hash=inputs.manifest.content_fingerprint(),
            golden_dataset_hash=golden.content_hash(),
            rf_snapshot_id=inputs.rf_snapshot.snapshot_id,
            rf_snapshot_hash=inputs.rf_snapshot.content_hash,
            embedding_model_id=semantic.embedding_model_id,
            extractor_version=versions["extractor"],
            classifier_version=versions["persecution_classifier"],
            matcher_version=versions["rosfinmonitoring_matcher"],
            resolver_version=versions["entity_resolution"],
            component_versions=versions,
            policy_version=inputs.policy.policy_version,
            policy_hash=inputs.policy_hash,
            thresholds={
                "hard_gates": {k: float(v) for k, v in inputs.policy.hard_gates.items()},
                "quality_targets": dict(inputs.policy.quality_targets),
                "advisory_targets": dict(inputs.policy.advisory_targets),
            },
            split=options.split.value if options.split else "all",
            verified_only=verified_only,
            scenarios=[s.name for s in monitoring.scenarios],
            timestamp=started_at,
        ),
        dataset=dataset_summary(inputs, selected),
        extraction=extraction,
        entity_resolution=entity_resolution,
        persecution=persecution,
        rosfinmonitoring=rosfin,
        candidate_query=candidates,
        retrieval=retrieval,
        research=research,
        monitoring=monitoring,
        performance=perf,
        safety_gates=gates,
        failures=failures,
        failure_summary=failure_summary(failures),
        known_limitations=limitations,
        recommended_next_work=recommended_next_work(gates, failures),
    )
    return report


def finding_timing(
    dataset: GoldenDataset,
    identity: IdentityMap,
    session_factory: sessionmaker[Session],
    run_period: dict[int, str],
    manifest: CorpusManifest,
) -> dict[str, int]:
    """Expected actionable persons: finding not before evidence, not after the first sufficient run."""
    periods = finding_periods(session_factory, run_period)
    order = [p.value for p in TemporalPeriod]
    split_of = {a.key: a.corpus_split.value for a in manifest.articles}
    counts: Counter[str] = Counter()
    for person in dataset.persons:
        candidate = person.candidate
        if candidate is None or not candidate.expected_main_candidate:
            continue
        person_id = identity.mapped_person(person.golden_person_id)
        actual = periods.get(person_id) if person_id is not None else None
        evidence_periods = [
            split_of[a.key]
            for a in dataset.articles_of(person.golden_person_id)
            if a.key in split_of
        ]
        earliest = min(evidence_periods, key=order.index) if evidence_periods else None
        expected = (
            candidate.expected_first_finding_period.value
            if candidate.expected_first_finding_period
            else earliest
        )
        if actual is None:
            counts["missing"] += 1
        elif earliest is not None and order.index(actual) < order.index(earliest):
            counts["before_evidence"] += 1
        elif expected is not None and order.index(actual) > order.index(expected):
            counts["late"] += 1
        else:
            counts["on_time"] += 1
    return {key: counts.get(key, 0) for key in ("on_time", "late", "before_evidence", "missing")}


def default_rf_snapshot(golden: GoldenDataset) -> RfSnapshotFile:
    return RfSnapshotFile(
        snapshot_id=golden.version.rf_snapshot_id,
        path=REPOSITORY_ROOT / golden.version.rf_snapshot_path,
    )


def qdrant_url_from_env() -> str | None:
    return os.environ.get("QDRANT_TEST_URL") or None
