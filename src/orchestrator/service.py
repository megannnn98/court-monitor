"""The technical pipeline orchestrator: monitoring runs over the module facades (ADR 0013).

The orchestrator coordinates, the modules decide. Each stage asks a module's public
API what is still pending (the module reads it from PostgreSQL), calls that API once
per unit of work, in the module's own transaction, and records counters and failed
items on the run. It holds no domain query and takes no domain decision. The CLI calls
`run_source` / `run_derived`; Dagster assets call the same stage methods one by one.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import Counter
from collections.abc import Iterator, Sequence
from contextlib import aclosing, contextmanager
from dataclasses import dataclass, field, replace
from datetime import date
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import Engine

from analysis.service import AnalysisService
from ingestion.service import IngestionService, SkipReason
from monitor_core.model import SourceReference
from orchestrator.locks import advisory_lock
from orchestrator.models import (
    DERIVED_SCOPE,
    MonitoringRunAbortedError,
    MonitoringRunStatus,
    MonitoringRunView,
    MonitoringSettings,
    MonitoringStage,
    MonitoringStatusView,
    MonitoringTrigger,
    source_scope,
)
from orchestrator.repository import SqlAlchemyMonitoringRepository
from persons.resolution.stage import PersonResolutionStage
from processing.service import ExtractionRunStatus, ProcessingService
from results.service import NO_RF_SNAPSHOT, ResultsService

logger = logging.getLogger("monitoring")

ENTITY_REVIEW_NOT_CONFIGURED = "not_configured"
# How deep a regular run goes when it cannot stop at what is stored (a source's first
# load, the load after one that broke). Deeper is an explicit backfill.
FIRST_LOAD_LIMIT = 500
# How many pending decisions a model reviews in one run, at most.
ENTITY_REVIEW_BATCH = 50
# A run waiting for a derived-stage lock refreshes its heartbeat at least this often.
MAX_LOCK_WAIT_HEARTBEAT_SECONDS = 60.0
# The resolution stage records how far it got every this many extraction runs.
RESOLUTION_PROGRESS_EVERY = 10


class ExtractionFailedError(Exception):
    """Extraction stored a FAILED run for an article (deterministic, not retried)."""


@dataclass(frozen=True)
class RunHandle:
    """What stages pass along: identifiers and parameters, never article bodies."""

    run_id: int
    source: str | None
    trigger: MonitoringTrigger
    discovery_limit: int = 0
    refetch_known: bool = False
    published_from: date | None = None
    published_to: date | None = None
    # Resolution only: nothing was discovered, so the checkpoint must not move.
    resolve_only: bool = False

    @property
    def is_regular(self) -> bool:
        return not self.resolve_only and self.trigger in (
            MonitoringTrigger.SCHEDULE,
            MonitoringTrigger.MANUAL,
        )


@dataclass(frozen=True)
class DiscoveryResult:
    references: tuple[SourceReference, ...] = ()
    known_external_ids: frozenset[str] = frozenset()

    def to_ingest(self, *, refetch_known: bool) -> list[SourceReference]:
        return [
            reference
            for reference in self.references
            if refetch_known or reference.external_id not in self.known_external_ids
        ]


@dataclass(frozen=True)
class IngestionResult:
    article_ids: tuple[int, ...] = ()
    failed: int = 0


@dataclass(frozen=True)
class StageResult:
    """Small, serializable stage outcome (Dagster metadata, CLI output)."""

    processed: int = 0
    created: int = 0
    skipped: int = 0
    failed: int = 0
    reviews: int = 0
    metrics: dict[str, Any] = field(default_factory=dict)


class DryRunPlan(BaseModel):
    source: str
    discovery_limit: int
    discovered: int
    estimated_new: int
    references: list[SourceReference] = Field(default_factory=list)
    new_references: list[SourceReference] = Field(default_factory=list)


@dataclass(frozen=True)
class PipelineModules:
    """The public APIs the orchestrator calls, one per module, and its own run state."""

    engine: Engine
    repository: SqlAlchemyMonitoringRepository
    ingestion: IngestionService
    processing: ProcessingService
    # Person resolution (ER v2): the optional path of ADR 0024, kept for this run.
    resolution: PersonResolutionStage
    analysis: AnalysisService
    results: ResultsService


class MonitoringService:
    def __init__(self, modules: PipelineModules, settings: MonitoringSettings) -> None:
        self._deps = modules
        self._settings = settings
        self._repository = modules.repository

    @property
    def settings(self) -> MonitoringSettings:
        return self._settings

    @property
    def repository(self) -> SqlAlchemyMonitoringRepository:
        return self._repository

    def _source_base_url(self, name: str) -> str:
        """ValueError for a source nobody registered."""
        return self._deps.ingestion.source_base_url(name)

    @contextmanager
    def _derived_lock(self, handle: RunHandle, stage: str) -> Iterator[None]:
        """Serialize a global stage across runs; keep the heartbeat alive while waiting."""
        heartbeat_every = min(
            MAX_LOCK_WAIT_HEARTBEAT_SECONDS, self._settings.stale_run_after.total_seconds() / 4
        )
        with advisory_lock(
            self._deps.engine,
            f"monitoring:derived:{stage}",
            while_waiting=lambda: self._repository.heartbeat(handle.run_id),
            wait_callback_every_seconds=heartbeat_every,
            poll_seconds=min(1.0, heartbeat_every),
        ):
            yield

    @contextmanager
    def _stage(self, handle: RunHandle, stage: MonitoringStage) -> Iterator[dict[str, Any]]:
        """Time a stage, store its metrics on the run and log `monitoring_<stage>_completed`."""
        # Fencing: an aborted run does not start another stage.
        self._repository.heartbeat(handle.run_id)
        metrics: dict[str, Any] = {}
        started = time.monotonic()
        yield metrics
        metrics["duration_ms"] = round((time.monotonic() - started) * 1000)
        self._repository.set_stage_metrics(handle.run_id, stage, metrics)
        logger.info(
            "event=monitoring_%s_completed run_id=%s source=%s stage=%s metrics=%s",
            _LOG_STAGE_NAMES[stage],
            handle.run_id,
            handle.source,
            stage.value,
            metrics,
        )

    # -- run lifecycle ---------------------------------------------------------------------

    def start_source_run(
        self,
        source: str,
        *,
        trigger: MonitoringTrigger = MonitoringTrigger.MANUAL,
        discovery_limit: int | None = None,
        refetch_known: bool = False,
        published_from: date | None = None,
        published_to: date | None = None,
    ) -> RunHandle:
        """Raises `MonitoringAlreadyRunningError` when this source is already being monitored."""
        self._source_base_url(source)
        if refetch_known and trigger is not MonitoringTrigger.BACKFILL:
            raise ValueError("refetch_known is only allowed for a backfill")
        if (
            published_from is not None
            and published_to is not None
            and published_from > published_to
        ):
            raise ValueError("published_from must be <= published_to")
        limit = discovery_limit or self._settings.discovery_limit
        parameters: dict[str, Any] = {"discovery_limit": limit, "refetch_known": refetch_known}
        if published_from is not None:
            parameters["published_from"] = published_from.isoformat()
        if published_to is not None:
            parameters["published_to"] = published_to.isoformat()
        run_id = self._repository.start_run(
            scope=source_scope(source),
            source=source,
            trigger=trigger,
            parameters=parameters,
            stale_after=self._settings.stale_run_after,
        )
        return RunHandle(
            run_id=run_id,
            source=source,
            trigger=trigger,
            discovery_limit=limit,
            refetch_known=refetch_known,
            published_from=published_from,
            published_to=published_to,
        )

    def start_derived_run(
        self, *, trigger: MonitoringTrigger = MonitoringTrigger.DERIVED
    ) -> RunHandle:
        run_id = self._repository.start_run(
            scope=DERIVED_SCOPE,
            source=None,
            trigger=trigger,
            parameters={},
            stale_after=self._settings.stale_run_after,
        )
        return RunHandle(run_id=run_id, source=None, trigger=trigger)

    def finish(self, handle: RunHandle, *, error: BaseException | None = None) -> MonitoringRunView:
        status = self._repository.finish_run(handle.run_id, error=error)
        run = self._repository.get_run(handle.run_id)
        if run is None:
            raise LookupError(f"Monitoring run {handle.run_id} not found")
        # Only regular monitoring moves the checkpoint; backfill and derived runs never do.
        if (
            handle.is_regular
            and handle.source is not None
            and status in (MonitoringRunStatus.COMPLETED, MonitoringRunStatus.COMPLETED_WITH_ERRORS)
        ):
            self._repository.update_checkpoint(
                handle.source, run_id=handle.run_id, discovered_count=run.documents_discovered
            )
        event = (
            "monitoring_run_failed"
            if status is MonitoringRunStatus.FAILED
            else "monitoring_run_completed"
        )
        logger.log(
            logging.ERROR if status is MonitoringRunStatus.FAILED else logging.INFO,
            "event=%s run_id=%s source=%s status=%s duration_s=%s discovered=%d ingested=%d skipped=%d "
            "failed=%d persons_created=%d persons_linked=%d reviews=%d classified=%d "
            "rf_matched=%d findings_created=%d errors=%d",
            event,
            run.id,
            run.source,
            run.status.value,
            run.duration_seconds,
            run.documents_discovered,
            run.documents_ingested,
            run.documents_skipped,
            run.documents_failed,
            run.persons_created,
            run.persons_linked,
            run.person_reviews_created,
            run.classifications_created,
            run.rf_matches_created,
            run.findings_created,
            run.error_count,
        )
        if handle.source is not None:
            self._warn_of_layout(handle.source)
        return run

    def _warn_of_layout(self, source: str) -> None:
        """A run that looks like the site changed its layout says so in the log, for a
        scheduled run too; the console shows the same (`orchestrator.source_health`)."""
        try:
            suspects = self._repository.layout_suspects(source)
        except Exception:
            # The check is a warning; it must never fail the run it follows.
            logger.exception("event=source_layout_check_failed source=%s", source)
            return
        for suspect in suspects:
            logger.warning(
                "event=source_layout_suspect source=%s signal=%s run_id=%s count=%d total=%d "
                "example=%s",
                suspect.source,
                suspect.signal,
                suspect.run_id,
                suspect.count,
                suspect.total,
                suspect.example or "-",
            )

    def run_source(
        self,
        source: str,
        *,
        trigger: MonitoringTrigger = MonitoringTrigger.MANUAL,
        discovery_limit: int | None = None,
        refetch_known: bool = False,
        published_from: date | None = None,
        published_to: date | None = None,
        with_derived: bool = True,
        with_resolution: bool = True,
    ) -> MonitoringRunView:
        """One full source run: source stages, then the derived stages.

        `with_derived=False` stops after resolution, for a catch-up over many sources that
        runs the derived stages once at the end; `with_resolution=False` also skips
        resolution, left to `resolve_source` (it works through the source's whole backlog)."""
        if with_derived and not with_resolution:
            raise ValueError("the derived stages need resolution")
        handle = self.start_source_run(
            source,
            trigger=trigger,
            discovery_limit=discovery_limit,
            refetch_known=refetch_known,
            published_from=published_from,
            published_to=published_to,
        )
        try:
            discovery = self.discover(handle)
            ingestion = self.ingest(handle, discovery)
            self.extract(handle, ingestion)
            if with_resolution:
                self.resolve(handle)
            if with_derived:
                self._run_derived_stages(handle)
        except MonitoringRunAbortedError:
            logger.warning(
                "event=monitoring_run_fenced run_id=%s: aborted while running", handle.run_id
            )
            return self.finish(handle)
        except Exception as exc:
            logger.exception(
                "event=monitoring_run_failed run_id=%s source=%s", handle.run_id, source
            )
            return self.finish(handle, error=exc)
        except BaseException as exc:
            # Interrupted (Ctrl+C, SIGTERM): release the scope now, not after the stale timeout.
            self.finish(handle, error=exc)
            raise
        return self.finish(handle)

    def resolve_source(
        self, source: str, *, trigger: MonitoringTrigger = MonitoringTrigger.MANUAL
    ) -> MonitoringRunView:
        """Only the resolution stage of one source, over everything it has pending.

        Holds the source's scope, so it never runs beside a load of the same source."""
        handle = replace(
            self.start_source_run(source, trigger=trigger, discovery_limit=1), resolve_only=True
        )
        try:
            self.resolve(handle)
        except MonitoringRunAbortedError:
            logger.warning(
                "event=monitoring_run_fenced run_id=%s: aborted while running", handle.run_id
            )
            return self.finish(handle)
        except Exception as exc:
            logger.exception(
                "event=monitoring_run_failed run_id=%s source=%s scope=resolution",
                handle.run_id,
                source,
            )
            return self.finish(handle, error=exc)
        except BaseException as exc:
            # Interrupted (Ctrl+C, SIGTERM): release the scope now, not after the stale timeout.
            self.finish(handle, error=exc)
            raise
        return self.finish(handle)

    def run_derived(
        self, *, trigger: MonitoringTrigger = MonitoringTrigger.DERIVED
    ) -> MonitoringRunView:
        """Classification, RF matching and findings without any web access."""
        handle = self.start_derived_run(trigger=trigger)
        try:
            self._run_derived_stages(handle)
        except MonitoringRunAbortedError:
            logger.warning(
                "event=monitoring_run_fenced run_id=%s: aborted while running", handle.run_id
            )
            return self.finish(handle)
        except Exception as exc:
            logger.exception("event=monitoring_run_failed run_id=%s scope=derived", handle.run_id)
            return self.finish(handle, error=exc)
        except BaseException as exc:
            # Interrupted (Ctrl+C, SIGTERM): release the scope now, not after the stale timeout.
            self.finish(handle, error=exc)
            raise
        return self.finish(handle)

    def _run_derived_stages(self, handle: RunHandle) -> None:
        self.review_entities(handle)
        self.classify(handle)
        snapshot_id = self.match_rosfinmonitoring(handle)
        self.evaluate_findings(handle, snapshot_id=snapshot_id)

    # -- source stages ---------------------------------------------------------------------

    def dry_run(self, source: str, *, discovery_limit: int | None = None) -> DryRunPlan:
        """Discovery only: no run row, no ingestion, no checkpoint change."""
        ingestion = self._deps.ingestion
        ingestion.source_base_url(source)
        limit = discovery_limit or self._settings.discovery_limit
        references = asyncio.run(ingestion.discover(source, limit=limit))
        known = ingestion.known_external_ids(
            source, [reference.external_id for reference in references]
        )
        new = [reference for reference in references if reference.external_id not in known]
        return DryRunPlan(
            source=source,
            discovery_limit=limit,
            discovered=len(references),
            estimated_new=len(new),
            references=references,
            new_references=new,
        )

    def discover(self, handle: RunHandle) -> DiscoveryResult:
        """Discovery failures (after the source layer's own retries) fail the run."""
        source = _require_source(handle)
        ingestion = self._deps.ingestion
        ingestion.source_base_url(source)
        with self._stage(handle, MonitoringStage.DISCOVERY) as metrics:
            # A backfill goes deep on purpose; a regular run only needs what is new.
            stop_at_known = (
                handle.is_regular
                and not handle.refetch_known
                and handle.source is not None
                and self._repository.previous_load_completed(
                    handle.source, before_run_id=handle.run_id
                )
            )
            limit = handle.discovery_limit
            if handle.is_regular and not stop_at_known:
                # A source's first load, or the one after a load that broke: nothing stored
                # ends the paging, and the ceiling alone would be thousands of publications.
                limit = min(limit, FIRST_LOAD_LIMIT)
            references = asyncio.run(
                ingestion.discover(source, limit=limit, stop_at_known=stop_at_known)
            )
            known = ingestion.known_external_ids(
                source, [reference.external_id for reference in references]
            )
            skipped = 0 if handle.refetch_known else len(known)
            self._repository.add_counters(
                handle.run_id,
                {"documents_discovered": len(references), "documents_skipped": skipped},
            )
            metrics.update(discovered=len(references), known=len(known), unchanged_skipped=skipped)
        return DiscoveryResult(references=tuple(references), known_external_ids=frozenset(known))

    async def _ingest_references(
        self, handle: RunHandle, source: str, references: Sequence[SourceReference]
    ) -> tuple[list[int], Counter[str]]:
        article_ids: list[int] = []
        outcomes: Counter[str] = Counter()
        async with (
            self._deps.ingestion.open(
                source, published_from=handle.published_from, published_to=handle.published_to
            ) as documents,
            aclosing(
                documents.read_many(references, workers=self._deps.ingestion.download_workers)
            ) as results,
        ):
            async for reference, result in results:
                self._repository.heartbeat(handle.run_id)
                if isinstance(result, Exception):
                    exc = result
                    kind = self._repository.record_failure(
                        handle.run_id,
                        stage=MonitoringStage.INGESTION,
                        entity_type="source_reference",
                        external_ref=reference.url,
                        error=exc,
                    )
                    self._repository.add_counters(handle.run_id, {"documents_failed": 1})
                    outcomes[f"failed_{kind.value}"] += 1
                    continue
                document = result
                if document.skipped is SkipReason.NO_TEXT:
                    # Nothing to read, nothing broken: a skip, not a failure of the run.
                    logger.info(
                        "event=monitoring_document_skipped run_id=%s url=%s reason=no_text",
                        handle.run_id,
                        reference.url,
                    )
                    self._repository.add_counters(handle.run_id, {"documents_skipped": 1})
                    outcomes["skipped_no_text"] += 1
                    continue
                if document.skipped is not None:
                    reason = f"skipped_{document.skipped.value}"
                    logger.info(
                        "event=monitoring_document_skipped run_id=%s url=%s reason=%s published_at=%s",
                        handle.run_id,
                        reference.url,
                        reason,
                        document.published_at.isoformat() if document.published_at else None,
                    )
                    self._repository.add_counters(handle.run_id, {"documents_skipped": 1})
                    outcomes[reason] += 1
                    continue
                article_ids.append(documents.store(document))
                self._repository.add_counters(handle.run_id, {"documents_ingested": 1})
                outcomes["ingested"] += 1
        return article_ids, outcomes

    def ingest(self, handle: RunHandle, discovery: DiscoveryResult) -> IngestionResult:
        """Known documents are not fetched again (unless a backfill refetches); a failed
        document is recorded and does not stop the others."""
        source = _require_source(handle)
        self._source_base_url(source)
        references = discovery.to_ingest(refetch_known=handle.refetch_known)
        with self._stage(handle, MonitoringStage.INGESTION) as metrics:
            article_ids, outcomes = asyncio.run(self._ingest_references(handle, source, references))
            metrics.update(attempted=len(references), **outcomes)
        return IngestionResult(
            article_ids=tuple(article_ids),
            failed=sum(count for key, count in outcomes.items() if key.startswith("failed")),
        )

    def extract(self, handle: RunHandle, ingestion: IngestionResult | None = None) -> StageResult:
        """Articles of the source never extracted with the current versions, plus the ones
        this run (re)ingested. Survives a crash: pending work is read from the database."""
        processing = self._deps.processing
        pending = processing.articles_pending_extraction(
            source_base_url=self._source_base_url(_require_source(handle))
        )
        article_ids = sorted(set(pending) | set(ingestion.article_ids if ingestion else ()))
        processed = skipped = failed = events = 0
        with self._stage(handle, MonitoringStage.EXTRACTION) as metrics:
            for article_id in article_ids:
                self._repository.heartbeat(handle.run_id)
                try:
                    saved = processing.extract_article(article_id)
                except Exception as exc:  # noqa: BLE001 - per-item isolation, recorded on the run
                    self._repository.record_failure(
                        handle.run_id,
                        stage=MonitoringStage.EXTRACTION,
                        entity_type="article",
                        entity_id=article_id,
                        error=exc,
                    )
                    failed += 1
                    continue
                if saved.status is not ExtractionRunStatus.SUCCEEDED:
                    self._repository.record_failure(
                        handle.run_id,
                        stage=MonitoringStage.EXTRACTION,
                        entity_type="article",
                        entity_id=article_id,
                        error=ExtractionFailedError(saved.error_message or "extraction failed"),
                    )
                    failed += 1
                elif saved.skipped_existing:
                    skipped += 1
                else:
                    processed += 1
                    events += saved.events_created
                    self._repository.add_counters(
                        handle.run_id,
                        {"articles_extracted": 1, "events_created": saved.events_created},
                    )
            metrics.update(
                pending=len(article_ids),
                extracted=processed,
                skipped_existing=skipped,
                failed=failed,
                events_created=events,
            )
        return StageResult(processed=processed, created=events, skipped=skipped, failed=failed)

    def resolve(self, handle: RunHandle) -> StageResult:
        """ER v2 on extraction runs with undecided person mentions. REVIEW is an outcome
        (counted), not a failure; unresolved mentions stay unlinked for downstream stages."""
        run_ids = self._deps.resolution.runs_pending_resolution(
            source_base_url=self._source_base_url(_require_source(handle))
        )
        created = linked = reviews = failed = 0
        with self._stage(handle, MonitoringStage.RESOLUTION) as metrics:
            for done, extraction_run_id in enumerate(run_ids):
                if done % RESOLUTION_PROGRESS_EVERY == 0:
                    # Progress for the operator console; the stage's own metrics replace it.
                    self._repository.set_stage_metrics(
                        handle.run_id,
                        MonitoringStage.RESOLUTION,
                        {"extraction_runs": len(run_ids), "done": done},
                    )
                else:
                    self._repository.heartbeat(handle.run_id)
                try:
                    stats = self._deps.resolution.resolve_run(extraction_run_id)
                except Exception as exc:  # noqa: BLE001 - per-item isolation, recorded on the run
                    self._repository.record_failure(
                        handle.run_id,
                        stage=MonitoringStage.RESOLUTION,
                        entity_type="extraction_run",
                        entity_id=extraction_run_id,
                        error=exc,
                    )
                    failed += 1
                    continue
                run_linked = max(
                    stats.mentions_resolved - stats.new_persons_created - stats.mentions_reused, 0
                )
                created += stats.new_persons_created
                linked += run_linked
                reviews += stats.reviews_pending
                self._repository.add_counters(
                    handle.run_id,
                    {
                        "persons_created": stats.new_persons_created,
                        "persons_linked": run_linked,
                        "person_reviews_created": stats.reviews_pending,
                    },
                )
            metrics.update(
                extraction_runs=len(run_ids),
                persons_created=created,
                persons_linked=linked,
                reviews=reviews,
                failed=failed,
            )
        return StageResult(
            processed=len(run_ids) - failed, created=created, failed=failed, reviews=reviews
        )

    # -- derived stages (global; serialized across concurrent runs) --------------------------

    def review_entities(self, handle: RunHandle) -> StageResult:
        """AI review of the pending ER decisions, then the action its policy allows.

        The reviewer is optional: without one the stage records `not_configured` and every
        pending decision stays with a human. A provider failure is an outcome of the
        review (counted, audited), never a failure of the stage."""
        with (
            self._derived_lock(handle, "ai_entity_review"),
            self._stage(handle, MonitoringStage.AI_ENTITY_REVIEW) as metrics,
        ):
            # Not the discovery ceiling itself: that one grew to let a run reach back, and
            # a review is a model's answer that costs money.
            batch = self._deps.resolution.review_pending(
                limit=min(self._settings.discovery_limit, ENTITY_REVIEW_BATCH)
            )
            if batch is None:
                metrics.update(status=ENTITY_REVIEW_NOT_CONFIGURED)
                return StageResult(metrics={"status": ENTITY_REVIEW_NOT_CONFIGURED})
            metrics.update(
                reviewed=batch.reviewed,
                auto_accepted=batch.auto_accepted,
                auto_rejected=batch.auto_rejected,
                human_required=batch.human_required,
                failed=batch.failed,
                skipped=batch.skipped,
            )
        return StageResult(
            processed=batch.reviewed,
            created=batch.auto_accepted + batch.auto_rejected,
            failed=batch.failed,
            reviews=batch.human_required,
        )

    def classify(self, handle: RunHandle) -> StageResult:
        analysis = self._deps.analysis
        statuses: Counter[str] = Counter()
        failed = 0
        with (
            self._derived_lock(handle, "classification"),
            self._stage(handle, MonitoringStage.CLASSIFICATION) as metrics,
        ):
            person_ids = analysis.persons_pending_classification()
            for person_id in person_ids:
                self._repository.heartbeat(handle.run_id)
                try:
                    status = analysis.classify_person(person_id)
                except Exception as exc:  # noqa: BLE001 - per-item isolation, recorded on the run
                    self._repository.record_failure(
                        handle.run_id,
                        stage=MonitoringStage.CLASSIFICATION,
                        entity_type="person",
                        entity_id=person_id,
                        error=exc,
                    )
                    failed += 1
                    continue
                statuses[status] += 1
                self._repository.add_counters(handle.run_id, {"classifications_created": 1})
            metrics.update(
                pending=len(person_ids),
                failed=failed,
                statuses=dict(statuses),
                classifier=analysis.person_classifier,
            )
        return StageResult(
            processed=sum(statuses.values()),
            failed=failed,
            reviews=statuses.get("needs_review", 0),
            metrics={"statuses": dict(statuses)},
        )

    def match_rosfinmonitoring(self, handle: RunHandle) -> int | None:
        """Match against the latest imported snapshot. Without a snapshot nothing is
        matched and nothing becomes NOT_MATCHED; returns the snapshot id used."""
        analysis = self._deps.analysis
        statuses: Counter[str] = Counter()
        failed = 0
        with (
            self._derived_lock(handle, "rf_matching"),
            self._stage(handle, MonitoringStage.RF_MATCHING) as metrics,
        ):
            snapshot_id = analysis.latest_snapshot_id()
            if snapshot_id is None:
                metrics.update(skipped=NO_RF_SNAPSHOT)
                return None
            self._repository.set_rf_snapshot(handle.run_id, snapshot_id)
            person_ids = analysis.persons_pending_rf_match(snapshot_id)
            for person_id in person_ids:
                self._repository.heartbeat(handle.run_id)
                try:
                    status = analysis.match_person(person_id, snapshot_id)
                except Exception as exc:  # noqa: BLE001 - per-item isolation, recorded on the run
                    self._repository.record_failure(
                        handle.run_id,
                        stage=MonitoringStage.RF_MATCHING,
                        entity_type="person",
                        entity_id=person_id,
                        error=exc,
                    )
                    failed += 1
                    continue
                statuses[status] += 1
                self._repository.add_counters(handle.run_id, {"rf_matches_created": 1})
            metrics.update(
                snapshot_id=snapshot_id,
                pending=len(person_ids),
                failed=failed,
                statuses=dict(statuses),
            )
        return snapshot_id

    def evaluate_findings(self, handle: RunHandle, *, snapshot_id: int | None) -> StageResult:
        with (
            self._derived_lock(handle, "findings"),
            self._stage(handle, MonitoringStage.FINDINGS) as metrics,
        ):
            evaluation = self._deps.results.evaluate_findings(
                run_id=handle.run_id, snapshot_id=snapshot_id
            )
            self._repository.add_counters(handle.run_id, {"findings_created": evaluation.created})
            metrics.update(
                snapshot_id=snapshot_id,
                skipped=evaluation.skipped_reason,
                criteria=[vars(item) for item in evaluation.criteria],
            )
        return StageResult(
            processed=sum(item.matched for item in evaluation.criteria),
            created=evaluation.created,
            metrics={"skipped": evaluation.skipped_reason},
        )

    # -- read side -------------------------------------------------------------------------

    def status(self) -> MonitoringStatusView:
        return self._repository.status()


_LOG_STAGE_NAMES = {
    MonitoringStage.DISCOVERY: "discovery",
    MonitoringStage.INGESTION: "ingestion",
    MonitoringStage.EXTRACTION: "extraction",
    MonitoringStage.RESOLUTION: "er",
    MonitoringStage.AI_ENTITY_REVIEW: "ai_entity_review",
    MonitoringStage.CLASSIFICATION: "classification",
    MonitoringStage.RF_MATCHING: "rf",
    MonitoringStage.SEMANTIC_INDEXING: "semantic",
    MonitoringStage.FINDINGS: "findings",
}


def _require_source(handle: RunHandle) -> str:
    if handle.source is None:
        raise ValueError(f"Monitoring run {handle.run_id} has no source")
    return handle.source
