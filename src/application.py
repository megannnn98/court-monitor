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
from ingestion.service import IngestionService
from ingestion.sources.source_registry import SOURCES, SourceDefinition
from ingestion.sources.sqlalchemy_persistence import SqlAlchemyIngestionPersistence
from ingestion.sources.website_adapter import WebsiteAdapter
from monitor_core.ingestion import RetryingDocumentFetcher
from monitor_core.ports import DocumentFetcher
from monitoring.findings import MonitoringFindingService, MonitoringQueryProvider
from monitoring.models import MonitoringSettings
from monitoring.repository import SqlAlchemyMonitoringRepository
from monitoring.selection import EVIDENCE_SETTLE_INTERVAL, SqlAlchemyMonitoringWorkQueries
from monitoring.service import MonitoringDependencies, MonitoringService
from persecution.classification_service import PersecutionClassificationService
from persons.persistence import SqlAlchemyPersonPersistence
from persons.resolution.ai_factory import build_entity_review_service
from persons.resolution.ai_policy import EntityReviewSettings
from persons.resolution.ai_review_service import AutomatedEntityReviewService
from persons.resolution.factory import build_person_resolution_service
from persons.resolution.resolution_service import ExtractionResolutionService
from processing.extraction.documents import SqlAlchemyExtractionDocumentRepository
from processing.extraction.events import RuleBasedEventExtractor
from processing.extraction.extractors import RuleBasedEntityExtractor
from processing.extraction.normalizers import RuleBasedMentionNormalizer
from processing.extraction.persistence import SqlAlchemyExtractionPersistence
from processing.extraction.pipeline import ExtractionPipeline
from processing.service import ProcessingService
from rosfinmonitoring.matcher import RuleBasedRosfinmonitoringMatcher
from rosfinmonitoring.matcher_persistence import RosfinMatchPersistence
from rosfinmonitoring.snapshot_lookup import SqlAlchemyRosfinmonitoringSnapshotLookup
from settings import ApplicationSettings

HTTP_TIMEOUT_SECONDS = 5.0
HTTP_USER_AGENT = "my-app/1.0"


def default_http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS, headers={"User-Agent": HTTP_USER_AGENT})


def default_fetcher() -> DocumentFetcher:
    # The only fetch retry layer: Dagster does not add retries on top of it.
    return RetryingDocumentFetcher(WebsiteAdapter(), max_attempts=3, base_delay_seconds=0.5)


def default_extraction_pipeline(session_factory: sessionmaker[Session]) -> ExtractionPipeline:
    return ExtractionPipeline(
        extractors=[RuleBasedEntityExtractor()],
        normalizers=[RuleBasedMentionNormalizer()],
        event_extractor=RuleBasedEventExtractor(),
        persistence=SqlAlchemyExtractionPersistence(session_factory),
    )


def build_ingestion_service(
    session_factory: sessionmaker[Session],
    *,
    sources: Mapping[str, SourceDefinition] = SOURCES,
    create_http_client: Callable[[], httpx.AsyncClient] = default_http_client,
    create_fetcher: Callable[[], DocumentFetcher] = default_fetcher,
) -> IngestionService:
    return IngestionService(
        sources=sources,
        stored=SqlAlchemyMonitoringWorkQueries(session_factory),
        create_http_client=create_http_client,
        create_fetcher=create_fetcher,
        create_persistence=lambda source: SqlAlchemyIngestionPersistence(
            session_factory=session_factory,
            source_name=source.source_name,
            source_base_url=source.base_url,
        ),
    )


def build_processing_service(
    session_factory: sessionmaker[Session],
    *,
    extraction_pipeline: ExtractionPipeline | None = None,
) -> ProcessingService:
    return ProcessingService(
        pipeline=extraction_pipeline or default_extraction_pipeline(session_factory),
        documents=SqlAlchemyExtractionDocumentRepository(session_factory),
        pending=SqlAlchemyMonitoringWorkQueries(session_factory),
    )


def build_monitoring_service(
    session_factory: sessionmaker[Session],
    *,
    settings: MonitoringSettings,
    env: Mapping[str, str] | None = None,
    sources: Mapping[str, SourceDefinition] = SOURCES,
    create_http_client: Callable[[], httpx.AsyncClient] = default_http_client,
    create_fetcher: Callable[[], DocumentFetcher] = default_fetcher,
    # None: built from the environment; ENTITY_REVIEW_PROVIDER=none leaves it unset and
    # every pending ER decision goes to a human (ADR 0020).
    entity_review: AutomatedEntityReviewService | None = None,
    query_provider: MonitoringQueryProvider | None = None,
    extraction_pipeline: ExtractionPipeline | None = None,
    evidence_settle_interval: timedelta = EVIDENCE_SETTLE_INTERVAL,
) -> MonitoringService:
    engine = session_factory.kw["bind"]
    person_persistence = SqlAlchemyPersonPersistence(session_factory)
    if entity_review is None:
        entity_review = build_entity_review_service(
            session_factory, EntityReviewSettings.from_env(env)
        )

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
        extraction_pipeline=extraction_pipeline or default_extraction_pipeline(session_factory),
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
        # Probable matches: names the operator's Airtable list has and the published
        # one does not. They never become confirmed and never make a candidate.
        entity_review=entity_review,
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
