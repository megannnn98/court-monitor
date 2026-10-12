"""Composition root: builds the object graph once from configuration.

Used by the monitoring CLI commands, the monitoring API endpoints and the
Dagster resources, so every entry point runs the same wiring. Plain
construction, no registry or service locator: callers receive typed objects.
"""

from __future__ import annotations

import functools
import importlib.util
import logging
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import timedelta

import httpx
from sqlalchemy.orm import Session, sessionmaker

from analysis.pending import EVIDENCE_SETTLE_INTERVAL, SqlAlchemyPendingPersonAnalysis
from analysis.persecution.classification_service import PersecutionClassificationService
from analysis.rosfinmonitoring.matcher import RuleBasedRosfinmonitoringMatcher
from analysis.rosfinmonitoring.matcher_persistence import RosfinMatchPersistence
from analysis.rosfinmonitoring.snapshot_lookup import SqlAlchemyRosfinmonitoringSnapshotLookup
from analysis.service import AnalysisService
from db.database import (
    create_database_engine,
    create_session_factory,
    with_statement_timeout,
)
from db.investigation_runs import SqlAlchemyInvestigationRuns
from ingestion.search import configured_telegram_channels, telegram_searches
from ingestion.service import IngestionService
from ingestion.sources.source_registry import SOURCES, SourceDefinition
from ingestion.sources.sqlalchemy_persistence import SqlAlchemyIngestionPersistence
from ingestion.sources.website_adapter import WebsiteAdapter
from ingestion.stored import SqlAlchemyStoredDocuments
from investigation.models import Limits
from investigation.service import InvestigationService
from investigation.services.external import SourceSearchRetriever
from investigation.services.model import investigation_model_from_env
from investigation.services.retrieval import (
    DensePublicationsRetriever,
    InvestigationRetriever,
    LexicalPublicationsRetriever,
    LocalDatabaseRetriever,
    StoredPublicationsRetriever,
    UnnamedFigurantRetriever,
)
from monitor_core.ingestion import RetryingDocumentFetcher
from monitor_core.ports import DocumentFetcher
from orchestrator.cycle import OperatorCycle
from orchestrator.models import MonitoringSettings
from orchestrator.repository import SqlAlchemyMonitoringRepository
from orchestrator.runs import MonitoringRuns
from orchestrator.service import MonitoringService, PipelineModules
from persons.persistence import SqlAlchemyPersonPersistence
from persons.resolution.ai_factory import build_entity_review_service
from persons.resolution.ai_policy import EntityReviewSettings
from persons.resolution.ai_review_service import AutomatedEntityReviewService
from persons.resolution.factory import build_person_resolution_service
from persons.resolution.pending import SqlAlchemyPendingResolution
from persons.resolution.resolution_service import ExtractionResolutionService
from persons.resolution.review import PersonResolutionReviewService
from persons.resolution.stage import PersonResolutionStage
from processing.extraction.documents import SqlAlchemyExtractionDocumentRepository
from processing.extraction.events import RuleBasedEventExtractor
from processing.extraction.extractors import RuleBasedEntityExtractor
from processing.extraction.normalizers import RuleBasedMentionNormalizer
from processing.extraction.pending import SqlAlchemyPendingExtraction
from processing.extraction.persistence import SqlAlchemyExtractionPersistence
from processing.extraction.pipeline import ExtractionPipeline, ExtractionVersions
from processing.identity.service import identity_service_from_env
from processing.junk.embedder import EmbeddingConfig, SentenceTransformerEmbedder
from processing.service import ProcessingService
from results.findings import MonitoringFindingService, MonitoringQueryProvider
from results.service import ResultsService
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
        stored=SqlAlchemyStoredDocuments(session_factory),
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
        session_factory=session_factory,
        pipeline=extraction_pipeline or default_extraction_pipeline(session_factory),
        documents=SqlAlchemyExtractionDocumentRepository(session_factory),
        pending=SqlAlchemyPendingExtraction(session_factory),
    )


def build_person_resolution_stage(
    session_factory: sessionmaker[Session],
    *,
    versions: ExtractionVersions,
    env: Mapping[str, str] | None = None,
    # None: built from the environment; ENTITY_REVIEW_PROVIDER=none leaves it unset and
    # every pending ER decision goes to a human (ADR 0020).
    entity_review: AutomatedEntityReviewService | None = None,
) -> PersonResolutionStage:
    person_persistence = SqlAlchemyPersonPersistence(session_factory)
    if entity_review is None:
        entity_review = build_entity_review_service(
            session_factory, EntityReviewSettings.from_env(env)
        )
    return PersonResolutionStage(
        resolution=ExtractionResolutionService(
            persistence=person_persistence,
            session_factory=session_factory,
            person_resolution=build_person_resolution_service(
                session_factory, env, persistence=person_persistence
            ),
        ),
        pending=SqlAlchemyPendingResolution(session_factory),
        versions=versions,
        entity_review=entity_review,
    )


def build_analysis_service(
    session_factory: sessionmaker[Session],
    *,
    evidence_settle_interval: timedelta = EVIDENCE_SETTLE_INTERVAL,
) -> AnalysisService:
    return AnalysisService(
        session_factory=session_factory,
        classification=PersecutionClassificationService(session_factory),
        rf_matcher=RuleBasedRosfinmonitoringMatcher(session_factory),
        rf_persistence=RosfinMatchPersistence(session_factory),
        snapshot_lookup=SqlAlchemyRosfinmonitoringSnapshotLookup(session_factory),
        pending=SqlAlchemyPendingPersonAnalysis(
            session_factory, evidence_settle_interval=evidence_settle_interval
        ),
    )


def build_results_service(
    session_factory: sessionmaker[Session],
    *,
    query_provider: MonitoringQueryProvider | None = None,
) -> ResultsService:
    return ResultsService(
        session_factory=session_factory,
        findings=MonitoringFindingService(session_factory, query_provider=query_provider),
    )


def build_monitoring_service(
    session_factory: sessionmaker[Session],
    *,
    settings: MonitoringSettings,
    env: Mapping[str, str] | None = None,
    sources: Mapping[str, SourceDefinition] = SOURCES,
    create_http_client: Callable[[], httpx.AsyncClient] = default_http_client,
    create_fetcher: Callable[[], DocumentFetcher] = default_fetcher,
    entity_review: AutomatedEntityReviewService | None = None,
    query_provider: MonitoringQueryProvider | None = None,
    extraction_pipeline: ExtractionPipeline | None = None,
    evidence_settle_interval: timedelta = EVIDENCE_SETTLE_INTERVAL,
) -> MonitoringService:
    """The orchestrator over the five module facades; it is given nothing else."""
    processing = build_processing_service(session_factory, extraction_pipeline=extraction_pipeline)
    modules = PipelineModules(
        engine=session_factory.kw["bind"],
        repository=SqlAlchemyMonitoringRepository(session_factory),
        ingestion=build_ingestion_service(
            session_factory,
            sources=sources,
            create_http_client=create_http_client,
            create_fetcher=create_fetcher,
        ),
        processing=processing,
        resolution=build_person_resolution_stage(
            session_factory,
            versions=processing.extraction_versions,
            env=env,
            entity_review=entity_review,
        ),
        analysis=build_analysis_service(
            session_factory, evidence_settle_interval=evidence_settle_interval
        ),
        results=build_results_service(session_factory, query_provider=query_provider),
    )
    return MonitoringService(modules, settings)


def build_operator_cycle(
    session_factory: sessionmaker[Session], *, env: Mapping[str, str] | None = None
) -> OperatorCycle:
    """The steps of the operator cycle over the same facades a monitoring run uses."""
    return OperatorCycle(
        session_factory=session_factory,
        processing=build_processing_service(session_factory),
        identity=identity_service_from_env(env),
        analysis=build_analysis_service(session_factory),
    )


DENSE_MODEL_ENV = "INVESTIGATION_DENSE_MODEL"


def dense_embedder_from_env(
    env: Mapping[str, str] | None = None,
) -> SentenceTransformerEmbedder | None:
    """The embedding model of the dense retrieval (`INVESTIGATION_DENSE_MODEL`, e.g.
    `intfloat/multilingual-e5-base`), or None: unset, or the optional `semantic` group
    is not installed. The model loads on its first query, not here."""
    values = os.environ if env is None else env
    model_id = (values.get(DENSE_MODEL_ENV) or "").strip()
    if not model_id:
        return None
    if importlib.util.find_spec("sentence_transformers") is None:
        logging.getLogger("investigation").warning(
            "event=investigation_dense_unavailable model=%s reason=semantic_group_missing",
            model_id,
        )
        return None
    return _dense_embedder(EmbeddingConfig.from_env(model_id, values))


@functools.cache
def _dense_embedder(config: EmbeddingConfig) -> SentenceTransformerEmbedder:
    """One embedder per model and device in a process: the API builds an investigation
    per request, and a model loaded per request would cost seconds each time."""
    return SentenceTransformerEmbedder(config)


def build_investigation_retrievers(
    session_factory: sessionmaker[Session], *, env: Mapping[str, str] | None = None
) -> list[InvestigationRetriever]:
    """The registry of an investigation: the local database (entity groups, the stored
    publications by name and by ranked text, unnamed figurants) and the configured
    public Telegram channels' own search (`INVESTIGATION_TELEGRAM_CHANNELS`, «none» for
    none). Nothing a request carries adds to it."""
    limits = Limits.from_env(env)
    # A search the run stopped waiting for must not go on in the database: its thread
    # cannot be stopped, its statement can.
    results = build_results_service(
        with_statement_timeout(session_factory, limits.search_timeout_seconds)
    )
    local: list[InvestigationRetriever] = [
        LocalDatabaseRetriever(results),
        StoredPublicationsRetriever(results),
        LexicalPublicationsRetriever(results),
        UnnamedFigurantRetriever(results),
    ]
    dense = dense_embedder_from_env(env)
    if dense is not None:
        local.append(DensePublicationsRetriever(results, dense))
    outside: list[InvestigationRetriever] = [
        SourceSearchRetriever(search, source="telegram")
        for search in telegram_searches(
            configured_telegram_channels(env), timeout_seconds=float(limits.search_timeout_seconds)
        )
    ]
    return [*local, *outside]


def build_investigation_service(
    session_factory: sessionmaker[Session], *, env: Mapping[str, str] | None = None
) -> InvestigationService:
    """An investigation over the registry above. A model reads the question and words
    the answer when OpenRouter is configured; without one the rules alone answer. Runs
    are stored (`investigation_runs`), so a result is read again by its id and an
    interrupted run is resumed."""
    return InvestigationService(
        retrievers=build_investigation_retrievers(session_factory, env=env),
        model=investigation_model_from_env(env),
        limits=Limits.from_env(env),
        runs=SqlAlchemyInvestigationRuns(session_factory),
    )


def build_monitoring_runs(session_factory: sessionmaker[Session]) -> MonitoringRuns:
    return MonitoringRuns(SqlAlchemyMonitoringRepository(session_factory))


def build_resolution_review_service(
    session_factory: sessionmaker[Session],
) -> PersonResolutionReviewService:
    """Human review of ER v2 decisions: the person endpoints' compatibility path."""
    return PersonResolutionReviewService(SqlAlchemyPersonPersistence(session_factory))


@dataclass(frozen=True)
class ApplicationServices:
    session_factory: sessionmaker[Session]
    monitoring_settings: MonitoringSettings
    monitoring: MonitoringService
    runs: MonitoringRuns
    results: ResultsService


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
        runs=build_monitoring_runs(session_factory),
        results=build_results_service(session_factory),
    )
