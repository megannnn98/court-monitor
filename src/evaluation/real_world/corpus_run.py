"""Run the cached corpus through the production monitoring pipeline.

    disposable database (truncated) → evaluation RF snapshot (optional)
    → for each temporal period: publish its articles to the replay sources
      → MonitoringService.run_source per source (ingest, extract, ER v2,
        classification, RF matching, semantic indexing, findings)

The pipeline code is the product's; only the web and the clock of the
evidence settle interval are replaced.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from application import build_monitoring_service
from db.database import create_session_factory
from db.maintenance import require_disposable_database, truncate_disposable_tables
from evaluation.real_world.corpus_cache import RawCorpusCache
from evaluation.real_world.models import (
    CorpusManifest,
    ManifestArticle,
    TemporalPeriod,
    sha256_bytes,
)
from evaluation.real_world.replay import (
    ReplayUpstream,
    UnusedReplayFetcher,
    load_replay_entries,
    replay_sources,
)
from evaluation.real_world.state_snapshot import table_counts
from monitoring.models import MonitoringRunView, MonitoringSettings
from monitoring.service import MonitoringService
from rosfinmonitoring.ingestion import RosfinmonitoringIngestionPipeline
from rosfinmonitoring.persistence import RosfinmonitoringPersistence
from semantic_retrieval.indexer import SemanticIndexer
from sources.source_registry import SOURCES, SourceDefinition

RF_SNAPSHOT_DATE = datetime(2026, 9, 1, tzinfo=UTC)
RF_SNAPSHOT_SOURCE_URL = "https://rosfinmonitoring.evaluation.invalid/"


@dataclass(frozen=True)
class RfSnapshotFile:
    snapshot_id: str
    path: Path

    @property
    def content_hash(self) -> str:
        return sha256_bytes(self.path.read_bytes())


def import_rf_snapshot(session_factory: sessionmaker[Session], snapshot: RfSnapshotFile) -> int:
    return (
        RosfinmonitoringIngestionPipeline(persistence=RosfinmonitoringPersistence(session_factory))
        .ingest(
            raw_content=snapshot.path.read_bytes(),
            source_url=f"{RF_SNAPSHOT_SOURCE_URL}{snapshot.snapshot_id}",
            snapshot_date=RF_SNAPSHOT_DATE,
        )
        .snapshot_id
    )


def articles_by_period(manifest: CorpusManifest) -> dict[TemporalPeriod, list[ManifestArticle]]:
    periods: dict[TemporalPeriod, list[ManifestArticle]] = {period: [] for period in TemporalPeriod}
    for article in sorted(manifest.articles, key=lambda a: (a.published_at, a.key)):
        periods[article.corpus_split].append(article)
    return periods


@dataclass
class PeriodRun:
    period: str
    articles: int
    runs: list[MonitoringRunView]
    counts_before: dict[str, int]
    counts_after: dict[str, int]
    seconds: float

    def new(self) -> dict[str, int]:
        return {
            name: self.counts_after[name] - self.counts_before.get(name, 0)
            for name in self.counts_after
        }


@dataclass
class CorpusRunner:
    """One disposable database, one replay upstream, one monitoring service."""

    engine: Engine
    manifest: CorpusManifest
    cache: RawCorpusCache
    create_semantic_indexer: Callable[[], SemanticIndexer] | None = None
    definitions: Mapping[str, SourceDefinition] = field(default_factory=lambda: dict(SOURCES))
    service_wrapper: Callable[[MonitoringService], MonitoringService] | None = None
    upstream: ReplayUpstream = field(init=False)
    session_factory: sessionmaker[Session] = field(init=False)
    service: MonitoringService = field(init=False)
    snapshot_id: int | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        require_disposable_database(self.engine)
        self.session_factory = create_session_factory(self.engine)
        entries, mismatched = load_replay_entries(
            self.cache, self.manifest.articles, self.definitions
        )
        if mismatched:
            raise ValueError(f"cached articles differ from the manifest: {mismatched[:5]}")
        self.upstream = ReplayUpstream(entries=entries)
        self.service = self._build_service()

    def _build_service(self) -> MonitoringService:
        names = tuple(sorted(self.upstream.entries))
        service = build_monitoring_service(
            self.session_factory,
            settings=MonitoringSettings(
                enabled_sources=names, discovery_limit=max(1, len(self.manifest.articles))
            ),
            env={},
            sources=replay_sources(self.upstream, self.definitions),
            create_fetcher=UnusedReplayFetcher,
            create_semantic_indexer=self.create_semantic_indexer,
            use_env_semantic_indexer=False,
            # Periods follow each other within seconds; waiting would only delay derived work.
            evidence_settle_interval=timedelta(0),
        )
        return self.service_wrapper(service) if self.service_wrapper else service

    def rebuild_service(self) -> None:
        """A fresh service over the same database and upstream (a restarted process)."""
        self.service = self._build_service()

    def reset(self, rf_snapshot: RfSnapshotFile | None) -> None:
        truncate_disposable_tables(self.engine)
        self.upstream.published.clear()
        self.snapshot_id = (
            import_rf_snapshot(self.session_factory, rf_snapshot) if rf_snapshot else None
        )

    def run_period(self, period: str, articles: Sequence[ManifestArticle]) -> PeriodRun:
        before = table_counts(self.engine)
        started = time.monotonic()
        self.upstream.publish(articles)
        runs = [self.service.run_source(name) for name in sorted(self.upstream.entries)]
        return PeriodRun(
            period=period,
            articles=len(articles),
            runs=runs,
            counts_before=before,
            counts_after=table_counts(self.engine),
            seconds=round(time.monotonic() - started, 3),
        )

    def run_derived(self) -> MonitoringRunView:
        return self.service.run_derived()
