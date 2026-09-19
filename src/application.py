"""Composition root: builds the object graph once from configuration.

Used by the monitoring CLI commands, the monitoring API endpoints and the
Dagster resources, so every entry point runs the same wiring. Plain
construction, no registry or service locator: callers receive typed objects.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import timedelta

import httpx
from sqlalchemy.orm import Session, sessionmaker

from db.database import create_database_engine, create_session_factory
from extraction.documents import SqlAlchemyExtractionDocumentRepository
from extraction.events import RuleBasedEventExtractor
from extraction.normalizers import RuleBasedMentionNormalizer
from extraction.persistence import SqlAlchemyExtractionPersistence
from extraction.person_ner.factory import build_entity_extractor
from extraction.pipeline import ExtractionPipeline
from extraction.resolution_service import ExtractionResolutionService
from monitoring.findings import MonitoringFindingService, MonitoringQueryProvider
from monitoring.models import MonitoringSettings
from monitoring.repository import SqlAlchemyMonitoringRepository
from monitoring.selection import EVIDENCE_SETTLE_INTERVAL, SqlAlchemyMonitoringWorkQueries
from monitoring.service import MonitoringDependencies, MonitoringService
from persecution.classification_service import PersecutionClassificationService
from persons.persistence import SqlAlchemyPersonPersistence
from persons.resolution.factory import build_person_resolution_service
from rosfinmonitoring.matcher import RuleBasedRosfinmonitoringMatcher
from rosfinmonitoring.matcher_persistence import RosfinMatchPersistence
from rosfinmonitoring.snapshot_lookup import SqlAlchemyRosfinmonitoringSnapshotLookup
from semantic_retrieval.factory import SemanticRetrievalConfig, create_semantic_components
from semantic_retrieval.indexer import SemanticIndexer
from settings import ApplicationSettings
from sources.retrying_fetcher import RetryingDocumentFetcher
from sources.source_adapter import DocumentFetcher
from sources.source_registry import SOURCES, SourceDefinition
from sources.sqlalchemy_persistence import SqlAlchemyIngestionPersistence
from sources.website_adapter import WebsiteAdapter

HTTP_TIMEOUT_SECONDS = 5.0
HTTP_USER_AGENT = "my-app/1.0"


def default_http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS, headers={"User-Agent": HTTP_USER_AGENT})


def default_fetcher() -> DocumentFetcher:
    # The only fetch retry layer: Dagster does not add retries on top of it.
    return RetryingDocumentFetcher(WebsiteAdapter(), max_attempts=3, base_delay_seconds=0.5)


def semantic_indexer_factory(
    session_factory: sessionmaker[Session], env: Mapping[str, str] | None = None
) -> Callable[[], SemanticIndexer] | None:
    """None when semantic retrieval is not configured; otherwise a lazy factory (no model
    load, no connection yet)."""
    config = SemanticRetrievalConfig.from_env(env)
    if not config.enabled:
        return None

    def create() -> SemanticIndexer:
        return create_semantic_components(
            session_factory, config, env, with_reranker=False
        ).indexer()

    return create


def build_monitoring_service(
    session_factory: sessionmaker[Session],
    *,
    settings: MonitoringSettings,
    env: Mapping[str, str] | None = None,
    sources: Mapping[str, SourceDefinition] = SOURCES,
    create_http_client: Callable[[], httpx.AsyncClient] = default_http_client,
    create_fetcher: Callable[[], DocumentFetcher] = default_fetcher,
    create_semantic_indexer: Callable[[], SemanticIndexer] | None = None,
    use_env_semantic_indexer: bool = True,
    query_provider: MonitoringQueryProvider | None = None,
    extraction_pipeline: ExtractionPipeline | None = None,
    evidence_settle_interval: timedelta = EVIDENCE_SETTLE_INTERVAL,
) -> MonitoringService:
    engine = session_factory.kw["bind"]
    person_persistence = SqlAlchemyPersonPersistence(session_factory)
    if create_semantic_indexer is None and use_env_semantic_indexer:
        create_semantic_indexer = semantic_indexer_factory(session_factory, env)

    def ingestion_persistence(source: SourceDefinition) -> SqlAlchemyIngestionPersistence:
        return SqlAlchemyIngestionPersistence(
            session_factory=session_factory,
            source_name=source.source_name,
            source_base_url=source.base_url,
        )

    dependencies = MonitoringDependencies(
        engine=engine,
        repository=SqlAlchemyMonitoringRepository(session_factory),
        work=SqlAlchemyMonitoringWorkQueries(
            session_factory, evidence_settle_interval=evidence_settle_interval
        ),
        sources=sources,
        create_http_client=create_http_client,
        create_fetcher=create_fetcher,
        create_ingestion_persistence=ingestion_persistence,
        extraction_documents=SqlAlchemyExtractionDocumentRepository(session_factory),
        extraction_pipeline=extraction_pipeline
        or ExtractionPipeline(
            extractors=[build_entity_extractor()],
            normalizers=[RuleBasedMentionNormalizer()],
            event_extractor=RuleBasedEventExtractor(),
            persistence=SqlAlchemyExtractionPersistence(session_factory),
        ),
        resolution=ExtractionResolutionService(
            persistence=person_persistence,
            session_factory=session_factory,
            person_resolution=build_person_resolution_service(
                session_factory, env, persistence=person_persistence
            ),
        ),
        classification=PersecutionClassificationService(session_factory),
        rf_matcher=RuleBasedRosfinmonitoringMatcher(session_factory),
        rf_persistence=RosfinMatchPersistence(session_factory),
        snapshot_lookup=SqlAlchemyRosfinmonitoringSnapshotLookup(session_factory),
        findings=MonitoringFindingService(session_factory, query_provider=query_provider),
        create_semantic_indexer=create_semantic_indexer,
    )
    return MonitoringService(dependencies, settings)


@dataclass(frozen=True)
class ApplicationServices:
    session_factory: sessionmaker[Session]
    monitoring_settings: MonitoringSettings
    monitoring: MonitoringService
    monitoring_repository: SqlAlchemyMonitoringRepository
    findings: MonitoringFindingService


def build_application_services(
    *,
    database_url: str | None = None,
    session_factory: sessionmaker[Session] | None = None,
    env: Mapping[str, str] | None = None,
) -> ApplicationServices:
    env = os.environ if env is None else env
    if database_url is not None:
        env = {**env, "DATABASE_URL": database_url}
    # Raises ApplicationConfigurationError listing every invalid setting.
    application_settings = ApplicationSettings.from_env(
        env, require_database=session_factory is None
    )
    if session_factory is None:
        session_factory = create_session_factory(
            create_database_engine(
                application_settings.database_url, application_settings.database_pool
            )
        )
    settings = application_settings.monitoring
    monitoring = build_monitoring_service(session_factory, settings=settings, env=env)
    return ApplicationServices(
        session_factory=session_factory,
        monitoring_settings=settings,
        monitoring=monitoring,
        monitoring_repository=SqlAlchemyMonitoringRepository(session_factory),
        findings=MonitoringFindingService(session_factory),
    )
