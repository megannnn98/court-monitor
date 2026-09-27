"""End-to-end monitoring validation on the real corpus (RV4).

    temporal simulation   T0 baseline → monitor T1 → T2 → T3, each run repeated
    crash recovery        failure after a stage, restart, compare with a clean run
    source failures       timeout / HTTP 500 / malformed document stay isolated
    PostgreSQL failure    one article's bounded transaction fails, others commit
    no RF snapshot        nothing is reported as confirmed absence
    RF review statuses    never a main finding
    manual review         reviewer decision → derived processing, no re-ingestion

Each scenario uses the disposable evaluation database; the temporal run leaves
the final state that component evaluation reads.
"""

from __future__ import annotations

import logging
import re
import time
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import Engine, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from candidates.service import CandidateQueryService
from db.orm_models import MonitoringFindingRecord, MonitoringRunRecord
from evaluation.real_world.corpus_run import (
    CorpusRunner,
    PeriodRun,
    RfSnapshotFile,
    articles_by_period,
)
from evaluation.real_world.metrics import per_100
from evaluation.real_world.models import ManifestArticle
from evaluation.real_world.replay import ReplayFault
from evaluation.real_world.results import (
    GateStatus,
    PerformanceSection,
    PeriodResult,
    ScenarioResult,
    SectionStatus,
)
from evaluation.real_world.state_snapshot import (
    compare_snapshots,
    duplicate_counts,
    logical_snapshot,
    table_counts,
)
from monitoring.service import MonitoringService
from persons.persistence import SqlAlchemyPersonPersistence
from persons.resolution.review import PersonResolutionReviewService, ResolutionReviewAction

logger = logging.getLogger("evaluation.real_world")

# Rows a repeated run without upstream change must not add.
RERUN_TABLES = (
    "source_documents",
    "extraction_runs",
    "mentions",
    "persons",
    "aliases",
    "events",
    "person_event_links",
    "resolution_decisions",
    "classifications",
    "rf_results",
    "findings",
)
CRASH_STAGES = {
    "ingestion": "ingest",
    "extraction": "extract",
    "entity_resolution": "resolve",
    "classification": "classify",
}
_WORD = re.compile(r"\w+")


class InjectedCrash(RuntimeError):
    """Controlled failure injected after a completed stage."""


@dataclass
class TemporalOutcome:
    periods: list[PeriodResult] = field(default_factory=list)
    rerun_duplicates: dict[str, int] = field(default_factory=dict)
    run_period: dict[int, str] = field(default_factory=dict)
    period_runs: list[PeriodRun] = field(default_factory=list)
    seconds: float = 0.0


def run_temporal_simulation(
    runner: CorpusRunner, rf_snapshot: RfSnapshotFile | None, *, rerun: bool
) -> TemporalOutcome:
    outcome = TemporalOutcome()
    started = time.monotonic()
    runner.reset(rf_snapshot)
    duplicates: dict[str, int] = defaultdict(int)
    for period, articles in articles_by_period(runner.manifest).items():
        run = runner.run_period(period.value, articles)
        outcome.period_runs.append(run)
        for view in run.runs:
            outcome.run_period[view.id] = period.value
        rerun_new: dict[str, int] = {}
        if rerun:
            before = logical_snapshot(runner.engine)
            repeat = runner.run_period(f"{period.value}-rerun", [])
            for view in repeat.runs:
                outcome.run_period[view.id] = period.value
            rerun_new = {name: repeat.new().get(name, 0) for name in RERUN_TABLES}
            after = logical_snapshot(runner.engine)
            for name, count in rerun_new.items():
                duplicates[name] += max(0, count)
            for diff in compare_snapshots(before, after):
                # Logical changes without new upstream data are duplicates or churn.
                duplicates[f"logical_{diff.table}"] += diff.only_in_second
        outcome.periods.append(
            PeriodResult(
                period=period.value,
                articles_published=len(articles),
                run_status={view.source or "derived": view.status.value for view in run.runs},
                new={
                    name: run.new().get(name, 0)
                    for name in (
                        *RERUN_TABLES,
                        "pending_person_reviews",
                        "active_findings",
                    )
                },
                rerun_new=rerun_new,
                duration_seconds=run.seconds,
            )
        )
    for name, count in duplicate_counts(logical_snapshot(runner.engine)).items():
        duplicates[f"static_{name}"] += count
    outcome.rerun_duplicates = dict(sorted(duplicates.items()))
    outcome.seconds = round(time.monotonic() - started, 3)
    return outcome


def finding_periods(
    session_factory: sessionmaker[Session], run_period: Mapping[int, str]
) -> dict[int, str]:
    """Canonical person → temporal period of the run that first created its finding."""
    with session_factory() as session:
        rows = session.execute(
            select(MonitoringFindingRecord.person_id, MonitoringFindingRecord.first_seen_run_id)
        ).all()
    periods: dict[int, str] = {}
    for person_id, run_id in rows:
        period = run_period.get(run_id) if run_id is not None else None
        if period is not None:
            periods[person_id] = min(periods.get(person_id, period), period)
    return periods


def performance(engine: Engine, outcome: TemporalOutcome, articles: int) -> PerformanceSection:
    stage_ms: dict[str, float] = defaultdict(float)
    with engine.connect() as connection:
        for (metrics,) in connection.execute(select(MonitoringRunRecord.stage_metrics)).all():
            for stage, values in (metrics or {}).items():
                if isinstance(values, dict) and "duration_ms" in values:
                    stage_ms[stage] += float(values["duration_ms"])
        persons = int(connection.execute(text("SELECT count(*) FROM persons")).scalar_one())
    minutes = outcome.seconds / 60 if outcome.seconds else 0
    return PerformanceSection(
        status=SectionStatus.RUN,
        articles=articles,
        persons=persons,
        total_seconds=outcome.seconds,
        articles_per_minute=round(articles / minutes, 2) if minutes else None,
        persons_per_minute=round(persons / minutes, 2) if minutes else None,
        stage_seconds={stage: round(ms / 1000, 3) for stage, ms in sorted(stage_ms.items())},
    )


def review_workload(engine: Engine, articles: int) -> dict[str, float | int | None]:
    with engine.connect() as connection:
        er = int(
            connection.execute(
                text(
                    "SELECT count(*) FROM person_resolution_decisions WHERE status = 'pending_review'"
                )
            ).scalar_one()
        )
        persecution = int(
            connection.execute(
                text(
                    "SELECT count(*) FROM persecution_classifications c WHERE c.status IN "
                    "('needs_review', 'uncertain') AND c.id = (SELECT c2.id FROM "
                    "persecution_classifications c2 WHERE c2.person_id = c.person_id "
                    "ORDER BY c2.classified_at DESC, c2.id DESC LIMIT 1)"
                )
            ).scalar_one()
        )
        rf = int(
            connection.execute(
                text(
                    "SELECT count(*) FROM rosfin_matches WHERE status IN "
                    "('ambiguous', 'needs_review', 'insufficient_data')"
                )
            ).scalar_one()
        )
    return {
        "articles": articles,
        "er_reviews": er,
        "persecution_reviews": persecution,
        "rf_reviews": rf,
        "er_reviews_per_100_articles": per_100(er, articles),
        "persecution_reviews_per_100_articles": per_100(persecution, articles),
        "rf_reviews_per_100_articles": per_100(rf, articles),
        "total_reviews_per_100_articles": per_100(er + persecution + rf, articles),
    }


def rf_review_findings(
    session_factory: sessionmaker[Session], snapshot_id: int | None
) -> ScenarioResult:
    if snapshot_id is None:
        return ScenarioResult(
            name="rf_review_statuses_not_findings", status=GateStatus.NOT_RUN, detail="no snapshot"
        )
    with session_factory() as session:
        review_persons = set(
            session.scalars(
                text(
                    "SELECT person_id FROM rosfin_matches WHERE snapshot_id = :s AND status IN "
                    "('ambiguous', 'needs_review', 'insufficient_data')"
                ).bindparams(s=snapshot_id)
            ).all()
        )
        findings = set(
            session.scalars(
                select(MonitoringFindingRecord.person_id).where(
                    MonitoringFindingRecord.active.is_(True)
                )
            ).all()
        )
    candidates = {
        c.person_id
        for c in CandidateQueryService(session_factory)
        .get_candidates(snapshot_id, limit=None)
        .candidates
    }
    bad = len(review_persons & (findings | candidates))
    return ScenarioResult(
        name="rf_review_statuses_not_findings",
        status=GateStatus.PASS if bad == 0 else GateStatus.FAIL,
        detail=f"{len(review_persons)} persons with an RF review status; {bad} reached findings/candidates",
        metrics={"review_status_persons": len(review_persons), "in_findings_or_candidates": bad},
    )


# -- scenarios on a fresh database ------------------------------------------------------


# Crash stage -> (MonitoringStage metrics key, metric that counts the stage's real work).
CRASH_STAGE_WORK = {
    "ingestion": ("ingestion", "ingested"),
    "extraction": ("extraction", "extracted"),
    "entity_resolution": ("resolution", "extraction_runs"),
    "classification": ("classification", "pending"),
}


def _inject_after(service: MonitoringService, method: str, sources: frozenset[str]) -> None:
    """Crash once, after `method` completes inside a run of one of `sources`.

    Stage methods take the run handle first; runs of other sources (with no
    increment work) are left alone, so the crash always hits the tested path.
    """
    original = getattr(service, method)
    state = {"fired": False}

    def wrapped(*args: Any, **kwargs: Any) -> Any:
        result = original(*args, **kwargs)
        handle = args[0] if args else kwargs.get("handle")
        if not state["fired"] and getattr(handle, "source", None) in sources:
            state["fired"] = True
            raise InjectedCrash(f"injected failure after {method}")
        return result

    setattr(service, method, wrapped)


def crash_recovery(
    runner: CorpusRunner,
    rf_snapshot: RfSnapshotFile | None,
    baseline: Sequence[ManifestArticle],
    increment: Sequence[ManifestArticle],
) -> list[ScenarioResult]:
    """Uninterrupted reference vs. crash-after-stage + restart + rerun, per stage."""

    def prepare() -> None:
        runner.rebuild_service()
        runner.reset(rf_snapshot)
        runner.run_period("baseline", baseline)

    increment_sources = frozenset(article.source for article in increment)
    prepare()
    runner.run_period("increment", increment)
    reference = logical_snapshot(runner.engine)
    results = []
    for stage, method in CRASH_STAGES.items():
        prepare()
        _inject_after(runner.service, method, increment_sources)
        crashed = runner.run_period("increment-crash", increment)
        runner.rebuild_service()  # restart: a new process without the fault
        recovered = runner.run_period("increment-restart", [])
        diffs = compare_snapshots(reference, logical_snapshot(runner.engine))
        failed = [
            view
            for view in crashed.runs
            if view.status.value == "failed" and view.source in increment_sources
        ]
        stage_key, work_key = CRASH_STAGE_WORK[stage]
        work = (
            int((failed[0].stage_metrics.get(stage_key) or {}).get(work_key) or 0) if failed else 0
        )
        failed_first = bool(failed) and work > 0
        results.append(
            ScenarioResult(
                name=f"crash_after_{stage}",
                status=GateStatus.PASS if not diffs and failed_first else GateStatus.FAIL,
                detail=(
                    "restart reproduces the uninterrupted state"
                    if not diffs
                    else "; ".join(
                        f"{d.table}: -{d.only_in_first}/+{d.only_in_second} {d.examples[:2]}"
                        for d in diffs
                    )
                )
                + (
                    ""
                    if failed_first
                    else " (the injected failure did not stop a run with work for this stage)"
                ),
                metrics={
                    "crashed_source": failed[0].source or "" if failed else "",
                    "crashed_stage_work": work,
                    "crashed_run_status": ",".join(v.status.value for v in crashed.runs),
                    "restart_run_status": ",".join(v.status.value for v in recovered.runs),
                    "differing_tables": len(diffs),
                },
            )
        )
    runner.rebuild_service()
    return results


def source_failures(
    runner: CorpusRunner, rf_snapshot: RfSnapshotFile | None, articles: Sequence[ManifestArticle]
) -> ScenarioResult:
    faulty = list(articles[:3])
    if len(faulty) < 3:
        return ScenarioResult(
            name="source_failures", status=GateStatus.NOT_RUN, detail="fewer than 3 articles"
        )
    runner.rebuild_service()
    runner.reset(rf_snapshot)
    for article, fault in zip(
        faulty, (ReplayFault.TIMEOUT, ReplayFault.HTTP_500, ReplayFault.MALFORMED), strict=True
    ):
        runner.upstream.faults[article.key] = fault
    run = runner.run_period("faulty", articles)
    failed = sum(view.documents_failed for view in run.runs)
    ingested = sum(view.documents_ingested for view in run.runs)
    statuses = {view.status.value for view in run.runs if view.documents_failed}
    runner.upstream.faults.clear()
    retry = runner.run_period("retry", [])
    total = table_counts(runner.engine)["source_documents"]
    passed = (
        failed == 3
        and ingested == len(articles) - 3
        and statuses == {"completed_with_errors"}
        and total == len(articles)
    )
    return ScenarioResult(
        name="source_failures",
        status=GateStatus.PASS if passed else GateStatus.FAIL,
        detail=(
            f"timeout/HTTP 500/malformed: {failed} failed, {ingested} of {len(articles) - 3} others ingested, "
            f"runs {sorted(statuses)}; after the source recovered {total}/{len(articles)} documents "
            f"({','.join(v.status.value for v in retry.runs)})"
        ),
        metrics={"failed": failed, "ingested": ingested, "documents_after_retry": total},
    )


def postgres_interruption(
    runner: CorpusRunner, rf_snapshot: RfSnapshotFile | None, articles: Sequence[ManifestArticle]
) -> ScenarioResult:
    """A database error while extracting one article: bounded transaction, others commit."""
    runner.rebuild_service()
    runner.reset(rf_snapshot)
    documents = runner.service._deps.extraction_documents  # controlled failure injection
    original = documents.get_by_article_id
    target: dict[str, int | None] = {"article": None}

    def failing(article_id: int) -> Any:
        if target["article"] is None:
            target["article"] = article_id
        if article_id == target["article"]:
            raise OperationalError(
                "SELECT", {}, Exception("injected: server closed the connection")
            )
        return original(article_id)

    documents.get_by_article_id = failing  # type: ignore[method-assign]
    try:
        run = runner.run_period("pg-failure", articles)
    finally:
        documents.get_by_article_id = original  # type: ignore[method-assign]
    with runner.engine.connect() as connection:
        partial = int(
            connection.execute(
                text(
                    "SELECT count(*) FROM article_extraction_runs WHERE article_id = :a"
                ).bindparams(a=target["article"])
            ).scalar_one()
        )
        retryable = int(
            connection.execute(
                text(
                    "SELECT count(*) FROM monitoring_run_items WHERE stage = 'extraction' AND failure_kind = 'retryable'"
                )
            ).scalar_one()
        )
    extracted = table_counts(runner.engine)["extraction_runs"]
    retry = runner.run_period("pg-retry", [])
    after = table_counts(runner.engine)["extraction_runs"]
    passed = (
        partial == 0
        and retryable == 1
        and extracted == len(articles) - 1
        and after == len(articles)
    )
    return ScenarioResult(
        name="postgres_interruption",
        status=GateStatus.PASS if passed else GateStatus.FAIL,
        detail=(
            f"failed article left {partial} extraction rows, retryable items={retryable}, "
            f"{extracted}/{len(articles) - 1} others extracted, after retry {after}/{len(articles)} "
            f"({','.join(v.status.value for v in [*run.runs, *retry.runs])})"
        ),
        metrics={
            "partial_rows": partial,
            "retryable_items": retryable,
            "extracted_after_retry": after,
        },
    )


def no_rf_snapshot(runner: CorpusRunner) -> ScenarioResult:
    runner.rebuild_service()
    outcome = run_temporal_simulation(runner, None, rerun=False)
    with runner.engine.connect() as connection:
        findings = int(
            connection.execute(text("SELECT count(*) FROM monitoring_findings")).scalar_one()
        )
        matches = int(connection.execute(text("SELECT count(*) FROM rosfin_matches")).scalar_one())
    # Without RF results no person can be stated absent from the list.
    passed = findings == 0 and matches == 0
    return ScenarioResult(
        name="no_rf_snapshot",
        status=GateStatus.PASS if passed else GateStatus.FAIL,
        detail=f"{len(outcome.periods)} periods without a snapshot: findings={findings}, rf matches={matches}",
        metrics={"findings": findings, "rf_matches": matches},
    )


def manual_review_continuation(runner: CorpusRunner) -> ScenarioResult:
    """Take a pending ER review from the current state, decide it, run derived processing."""
    session_factory = runner.session_factory
    reviews = PersonResolutionReviewService(SqlAlchemyPersonPersistence(session_factory))
    with session_factory() as session:
        pending = reviews.list_pending(session, limit=1)
    if not pending:
        return ScenarioResult(
            name="manual_review_continuation",
            status=GateStatus.NOT_RUN,
            detail="no pending ER review",
        )
    review = pending[0]
    before = table_counts(runner.engine)
    fetches = len(runner.upstream.fetch_log)
    with session_factory.begin() as session:
        result = reviews.apply(
            session,
            review.decision_id,
            ResolutionReviewAction.CREATE_NEW_PERSON,
            note="real-world validation",
        )
    person_id = result.person_id
    derived = runner.run_derived()
    with runner.engine.connect() as connection:

        def one(sql: str) -> int:
            return int(connection.execute(text(sql).bindparams(p=person_id)).scalar_one())

        classified = one("SELECT count(*) FROM persecution_classifications WHERE person_id = :p")
        matched = one("SELECT count(*) FROM rosfin_matches WHERE person_id = :p")
        linked = one("SELECT count(*) FROM entity_mentions WHERE person_id = :p")
    after = table_counts(runner.engine)
    no_reingestion = (
        after["source_documents"] == before["source_documents"]
        and after["extraction_runs"] == before["extraction_runs"]
        and len(runner.upstream.fetch_log) == fetches
    )
    has_snapshot = runner.snapshot_id is not None
    passed = (
        linked > 0
        and classified > 0
        and (matched > 0 or not has_snapshot)
        and no_reingestion
        and derived.status.value != "failed"
    )
    return ScenarioResult(
        name="manual_review_continuation",
        status=GateStatus.PASS if passed else GateStatus.FAIL,
        detail=(
            f"decision {review.decision_id} ({review.incoming_name!r}) → create_new_person; derived run "
            f"{derived.status.value}: linked={linked}, classified={classified}, rf={matched}, "
            f"re-ingestion={not no_reingestion}"
        ),
        metrics={
            "linked_mentions": linked,
            "classifications": classified,
            "rf_matches": matched,
        },
    )
