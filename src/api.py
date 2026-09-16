"""FastAPI application for court-monitor read-only API."""

import logging
import os
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import asynccontextmanager
from csv import writer
from functools import lru_cache
from html import escape
from io import BytesIO, StringIO
from pathlib import Path
from urllib.parse import urlencode

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from candidates.service import CandidateQueryService
from db.database import DatabasePoolSettings, create_database_engine, create_session_factory
from db.orm_models import (
    ArticleExtractionRunRecord,
    ExtractedEventRecord,
    MonitoringRunRecord,
    ParsedArticleRecord,
    PersecutionClassificationRecord,
    PersonAliasRecord,
    PersonEventLinkRecord,
    PersonRecord,
    PersonResolutionDecisionRecord,
    RosfinMatchRecord,
    RosfinmonitoringEntryRecord,
    RosfinmonitoringSnapshotRecord,
    Source,
    SourceDocument,
)
from health import (
    LivenessReport,
    ReadinessChecker,
    ReadinessReport,
    ReadinessStatus,
    expected_schema_revision,
    qdrant_probe,
)
from monitoring.findings import MonitoringFindingService
from monitoring.models import (
    MonitoringFindingView,
    MonitoringRunDetails,
    MonitoringRunStatus,
    MonitoringRunView,
    MonitoringSettings,
    MonitoringStatusView,
)
from monitoring.repository import SqlAlchemyMonitoringRepository
from observability import REQUEST_ID_HEADER, configure_logging, normalize_request_id, request_id_var
from operator_console import (
    OPERATION_DEFINITIONS,
    OperationConflictError,
    OperationNotFoundError,
    OperationParameters,
    OperationRegistry,
    operation_run_to_dict,
)
from persecution.queries import latest_persecution_classification_ids
from persons.persistence import SqlAlchemyPersonPersistence
from persons.resolution.review import (
    PersonResolutionReviewService,
    ResolutionReviewAction,
    ResolutionReviewNotFoundError,
    ResolutionReviewResult,
    ResolutionReviewStateError,
    ResolutionReviewView,
)
from research.models import ResearchRequest, ResearchResponse
from research.repository import SqlAlchemyPersonResearchRepository
from research.review_tasks import (
    ResearchReviewConditionNotMetError,
    ResearchReviewSubjectNotFoundError,
    ResearchReviewTask,
    ResearchReviewTaskRequest,
    ResearchReviewTaskService,
)
from research.service import (
    ResearchCandidatesRequiredError,
    ResearchService,
    ResearchSnapshotNotFoundError,
)
from research.workflow.graph import ResearchGraph, run_research_query
from research.workflow.llm import LlmConfigurationError
from research.workflow.models import ResearchQueryResult, WorkflowErrorCode, WorkflowStatus
from research.workflow_factory import create_research_graph
from search.postgres_lexical import PostgresLexicalSearch
from semantic_retrieval.factory import SemanticRetrievalConfig
from semantic_retrieval.models import SemanticConfigurationError
from settings import ApplicationConfigurationError, ApplicationSettings
from sources.models import SearchHit, SearchQuery
from sources.source_registry import SOURCES

logger = logging.getLogger("api")
_OPERATION_REGISTRY = OperationRegistry()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Refuse to start with an invalid configuration (every problem listed).

    Dependencies are not required here: a database that is still starting makes
    `/health/ready` report unavailable instead of crashing the process.
    """
    configure_logging()
    try:
        ApplicationSettings.from_env()
    except ApplicationConfigurationError as exc:
        logger.error("event=config_invalid problems=%s", exc.problems)
        raise
    logger.info("event=api_started")
    yield
    logger.info("event=api_stopped")


# Create FastAPI app
app = FastAPI(
    title="Court Monitor API",
    description="Read-only API for court-monitor data",
    version="1.0.0",
    lifespan=lifespan,
)
app.mount("/static", StaticFiles(directory="src/static"), name="static")
# No CORS middleware: the API is meant for private deployment behind a reverse
# proxy (ADR 0014); browsers on other origins are not a supported client.


class ErrorBody(BaseModel):
    code: str
    message: str
    request_id: str


class ErrorResponse(BaseModel):
    error: ErrorBody


def error_response(status_code: int, code: str, message: str) -> JSONResponse:
    request_id = request_id_var.get()
    return JSONResponse(
        status_code=status_code,
        content=ErrorResponse(
            error=ErrorBody(code=code, message=message, request_id=request_id)
        ).model_dump(),
        headers={REQUEST_ID_HEADER: request_id},
    )


@app.middleware("http")
async def request_context(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """Request id for logs and responses; unhandled errors never leak a traceback."""
    request_id = normalize_request_id(request.headers.get(REQUEST_ID_HEADER))
    token = request_id_var.set(request_id)
    started = time.monotonic()
    try:
        try:
            response = await call_next(request)
        except Exception as exc:
            logger.exception(
                "event=http_unhandled_error method=%s path=%s error_kind=%s",
                request.method,
                request.url.path,
                type(exc).__name__,
            )
            response = error_response(500, "internal_error", "Internal server error")
        response.headers[REQUEST_ID_HEADER] = request_id
        logger.info(
            "event=http_request method=%s path=%s status=%s duration_ms=%d",
            request.method,
            request.url.path,
            response.status_code,
            (time.monotonic() - started) * 1000,
        )
        return response
    finally:
        request_id_var.reset(token)


# Database dependency
@lru_cache(maxsize=1)
def _get_session_factory() -> sessionmaker[Session]:
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL environment variable is not set")

    engine = create_database_engine(database_url, DatabasePoolSettings.from_env())
    return create_session_factory(engine)


def get_db() -> Iterator[Session]:
    """Get database session."""
    try:
        session_factory = _get_session_factory()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    with session_factory() as session:
        try:
            yield session
        finally:
            session.close()


# Pydantic models for API responses
class PersonResponse(BaseModel):
    """Person response model."""

    id: int
    canonical_name: str
    normalized_name: str
    matching_key: str
    status: str
    merged_into_id: int | None = None


class PersonAliasResponse(BaseModel):
    """Person alias response model."""

    id: int
    person_id: int
    surface_text: str
    normalized_text: str
    matching_key: str
    origin: str
    confidence: float


class PersecutionClassificationResponse(BaseModel):
    """Persecution classification response model."""

    id: int
    person_id: int
    status: str
    confidence: float
    reasons: list[str]
    evidence_types: list[str]
    classifier_name: str
    classifier_version: str


class CandidateResponse(BaseModel):
    """Candidate response model."""

    person_id: int
    canonical_name: str
    normalized_name: str
    persecution_status: str
    persecution_confidence: float
    persecution_reasons: list[str]
    rosfinmonitoring_status: str
    rosfinmonitoring_match_confidence: float | None = None
    event_count: int = 0
    alias_count: int = 0


class ArticleResponse(BaseModel):
    id: int
    source_name: str
    external_id: str
    title: str
    published_at: str | None
    url: str
    text: str


class EvidenceSpanResponse(BaseModel):
    article_id: int
    source_name: str
    title: str
    url: str
    start_offset: int
    end_offset: int
    text: str


class PersonEventResponse(BaseModel):
    id: int
    event_type: str
    event_date: str | None
    role: str
    confidence: float
    evidence: EvidenceSpanResponse


class LatestRosfinMatchResponse(BaseModel):
    snapshot_id: int
    status: str
    confidence: float
    matched_entry_id: int | None
    matched_entry_name: str | None
    reasons: list[str]


class PersonDetailResponse(BaseModel):
    person: PersonResponse
    aliases: list[PersonAliasResponse]
    persecution: PersecutionClassificationResponse | None
    rosfinmonitoring: LatestRosfinMatchResponse | None
    events: list[PersonEventResponse]


class RosfinmonitoringSnapshotResponse(BaseModel):
    """Rosfinmonitoring snapshot response model."""

    id: int
    snapshot_date: str
    source_url: str
    content_hash: str
    entry_count: int


class RosfinmonitoringEntryResponse(BaseModel):
    """Rosfinmonitoring entry response model."""

    id: int
    snapshot_id: int
    full_name: str
    normalized_name: str
    matching_key: str
    birth_date: str | None = None
    inclusion_reason: str | None = None


class ReviewResponse(BaseModel):
    """Review response model."""

    id: int
    subject_type: str
    subject_id: int
    decision: str | None = None
    confidence: float | None = None
    reason: str | None = None
    reviewer_note: str | None = None
    created_at: str
    reviewed_at: str | None = None


# Person endpoints
@app.get("/persons", response_model=list[PersonResponse])
def list_persons(
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    status: str | None = Query(default=None),
    db: Session = Depends(get_db),  # noqa: B008
) -> list[PersonResponse]:
    """List all persons."""
    # Ordered: offset pagination must not skip or repeat rows between pages.
    query = select(PersonRecord).order_by(PersonRecord.id).offset(offset).limit(limit)

    if status:
        query = query.where(PersonRecord.status == status)

    persons = db.scalars(query).all()

    return [
        PersonResponse(
            id=p.id,
            canonical_name=p.canonical_name,
            normalized_name=p.normalized_name,
            matching_key=p.matching_key,
            status=p.status,
            merged_into_id=p.merged_into_id,
        )
        for p in persons
    ]


@app.get("/persons/{person_id}", response_model=PersonResponse)
def get_person(
    person_id: int,
    db: Session = Depends(get_db),  # noqa: B008
) -> PersonResponse:
    """Get a person by ID."""
    person = db.get(PersonRecord, person_id)

    if not person:
        raise HTTPException(status_code=404, detail="Person not found")

    return PersonResponse(
        id=person.id,
        canonical_name=person.canonical_name,
        normalized_name=person.normalized_name,
        matching_key=person.matching_key,
        status=person.status,
        merged_into_id=person.merged_into_id,
    )


@app.get("/persons/{person_id}/aliases", response_model=list[PersonAliasResponse])
def get_person_aliases(
    person_id: int,
    db: Session = Depends(get_db),  # noqa: B008
) -> list[PersonAliasResponse]:
    """Get all aliases for a person."""
    person = db.get(PersonRecord, person_id)
    if not person:
        raise HTTPException(status_code=404, detail="Person not found")

    aliases = db.scalars(
        select(PersonAliasRecord).where(PersonAliasRecord.person_id == person_id)
    ).all()

    return [
        PersonAliasResponse(
            id=a.id,
            person_id=a.person_id,
            surface_text=a.surface_text,
            normalized_text=a.normalized_text,
            matching_key=a.matching_key,
            origin=a.origin,
            confidence=a.confidence,
        )
        for a in aliases
    ]


@app.get(
    "/persons/{person_id}/persecution",
    response_model=PersecutionClassificationResponse | None,
)
def get_person_persecution(
    person_id: int,
    db: Session = Depends(get_db),  # noqa: B008
) -> PersecutionClassificationResponse | None:
    """Get persecution classification for a person."""
    person = db.get(PersonRecord, person_id)
    if not person:
        raise HTTPException(status_code=404, detail="Person not found")

    classification = db.scalars(
        select(PersecutionClassificationRecord).where(
            PersecutionClassificationRecord.person_id == person_id,
            PersecutionClassificationRecord.id.in_(latest_persecution_classification_ids()),
        )
    ).first()

    if not classification:
        return None

    return PersecutionClassificationResponse(
        id=classification.id,
        person_id=classification.person_id,
        status=classification.status,
        confidence=classification.confidence,
        reasons=classification.reasons,
        evidence_types=classification.evidence_types,
        classifier_name=classification.classifier_name,
        classifier_version=classification.classifier_version,
    )


def _person_response(person: PersonRecord) -> PersonResponse:
    return PersonResponse(
        id=person.id,
        canonical_name=person.canonical_name,
        normalized_name=person.normalized_name,
        matching_key=person.matching_key,
        status=person.status,
        merged_into_id=person.merged_into_id,
    )


def _alias_response(alias: PersonAliasRecord) -> PersonAliasResponse:
    return PersonAliasResponse(
        id=alias.id,
        person_id=alias.person_id,
        surface_text=alias.surface_text,
        normalized_text=alias.normalized_text,
        matching_key=alias.matching_key,
        origin=alias.origin,
        confidence=alias.confidence,
    )


def _article_response(db: Session, article_id: int) -> ArticleResponse:
    row = db.execute(
        select(ParsedArticleRecord, SourceDocument, Source)
        .join(SourceDocument, SourceDocument.id == ParsedArticleRecord.document_id)
        .join(Source, Source.id == SourceDocument.source_id)
        .where(ParsedArticleRecord.id == article_id)
    ).one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Article not found")
    article, document, source = row
    return ArticleResponse(
        id=article.id,
        source_name=source.name,
        external_id=document.external_id,
        title=article.title,
        published_at=article.published_at.isoformat() if article.published_at else None,
        url=document.canonical_url,
        text=article.text,
    )


def _latest_persecution_response(
    db: Session, person_id: int
) -> PersecutionClassificationResponse | None:
    classification = db.scalars(
        select(PersecutionClassificationRecord).where(
            PersecutionClassificationRecord.person_id == person_id,
            PersecutionClassificationRecord.id.in_(latest_persecution_classification_ids()),
        )
    ).first()
    if classification is None:
        return None
    return PersecutionClassificationResponse(
        id=classification.id,
        person_id=classification.person_id,
        status=classification.status,
        confidence=classification.confidence,
        reasons=classification.reasons,
        evidence_types=classification.evidence_types,
        classifier_name=classification.classifier_name,
        classifier_version=classification.classifier_version,
    )


def _latest_rosfin_match_response(db: Session, person_id: int) -> LatestRosfinMatchResponse | None:
    match = db.scalars(
        select(RosfinMatchRecord)
        .where(RosfinMatchRecord.person_id == person_id)
        .order_by(RosfinMatchRecord.snapshot_id.desc(), RosfinMatchRecord.id.desc())
        .limit(1)
    ).first()
    if match is None:
        return None
    return LatestRosfinMatchResponse(
        snapshot_id=match.snapshot_id,
        status=match.status,
        confidence=match.confidence,
        matched_entry_id=match.matched_entry_id,
        matched_entry_name=match.matched_entry_name,
        reasons=match.reasons,
    )


@app.get("/articles/{article_id}", response_model=ArticleResponse)
def get_article(
    article_id: int,
    db: Session = Depends(get_db),  # noqa: B008
) -> ArticleResponse:
    """Full ParsedArticle text for evidence inspection."""
    return _article_response(db, article_id)


@app.get("/persons/{person_id}/events", response_model=list[PersonEventResponse])
def get_person_events(
    person_id: int,
    db: Session = Depends(get_db),  # noqa: B008
) -> list[PersonEventResponse]:
    """Events linked to a Person, each with its source article span."""
    person = db.get(PersonRecord, person_id)
    if person is None:
        raise HTTPException(status_code=404, detail="Person not found")

    rows = db.execute(
        select(
            PersonEventLinkRecord,
            ExtractedEventRecord,
            ParsedArticleRecord,
            SourceDocument,
            Source,
        )
        .join(ExtractedEventRecord, ExtractedEventRecord.id == PersonEventLinkRecord.event_id)
        .join(
            ArticleExtractionRunRecord,
            ArticleExtractionRunRecord.id == ExtractedEventRecord.extraction_run_id,
        )
        .join(ParsedArticleRecord, ParsedArticleRecord.id == ArticleExtractionRunRecord.article_id)
        .join(SourceDocument, SourceDocument.id == ParsedArticleRecord.document_id)
        .join(Source, Source.id == SourceDocument.source_id)
        .where(PersonEventLinkRecord.person_id == person_id)
        .order_by(
            ExtractedEventRecord.event_date.desc().nullslast(), ExtractedEventRecord.id.desc()
        )
    ).all()

    events: list[PersonEventResponse] = []
    for link, event, article, document, source in rows:
        events.append(
            PersonEventResponse(
                id=event.id,
                event_type=event.event_type,
                event_date=event.event_date.isoformat() if event.event_date else None,
                role=link.role,
                confidence=link.confidence,
                evidence=EvidenceSpanResponse(
                    article_id=article.id,
                    source_name=source.name,
                    title=article.title,
                    url=document.canonical_url,
                    start_offset=event.start_offset,
                    end_offset=event.end_offset,
                    text=article.text[event.start_offset : event.end_offset],
                ),
            )
        )
    return events


@app.get("/persons/{person_id}/detail", response_model=PersonDetailResponse)
def get_person_detail(
    person_id: int,
    db: Session = Depends(get_db),  # noqa: B008
) -> PersonDetailResponse:
    """Person card data: aliases, classifications, RF status and evidence-backed events."""
    person = db.get(PersonRecord, person_id)
    if person is None:
        raise HTTPException(status_code=404, detail="Person not found")
    aliases = db.scalars(
        select(PersonAliasRecord)
        .where(PersonAliasRecord.person_id == person_id)
        .order_by(PersonAliasRecord.id)
    ).all()
    return PersonDetailResponse(
        person=_person_response(person),
        aliases=[_alias_response(alias) for alias in aliases],
        persecution=_latest_persecution_response(db, person_id),
        rosfinmonitoring=_latest_rosfin_match_response(db, person_id),
        events=get_person_events(person_id, db),
    )


@app.get("/search/articles", response_model=list[SearchHit])
def search_articles(
    query: str = Query(..., min_length=1, max_length=500),
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),  # noqa: B008
) -> list[SearchHit]:
    """HTTP route over the existing PostgreSQL lexical search backend."""
    session_factory = sessionmaker(bind=db.get_bind())
    return PostgresLexicalSearch(session_factory).search(SearchQuery(text=query, limit=limit))


# Candidate endpoints
@app.get("/candidates", response_model=list[CandidateResponse])
def list_candidates(
    snapshot_id: int = Query(..., description="Rosfinmonitoring snapshot ID"),
    min_persecution_confidence: float = Query(default=0.7, ge=0.0, le=1.0),
    limit: int = Query(default=100, ge=1, le=1000),
    db: Session = Depends(get_db),  # noqa: B008
) -> list[CandidateResponse]:
    """List politically persecuted persons absent from Rosfinmonitoring."""
    service = CandidateQueryService(db)
    try:
        result = service.get_candidates(
            snapshot_id=snapshot_id,
            min_persecution_confidence=min_persecution_confidence,
            limit=limit,
            session=db,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return [
        CandidateResponse(
            person_id=c.person_id,
            canonical_name=c.canonical_name,
            normalized_name=c.normalized_name,
            persecution_status=c.persecution_status,
            persecution_confidence=c.persecution_confidence,
            persecution_reasons=c.persecution_reasons,
            rosfinmonitoring_status=c.rosfinmonitoring_status,
            rosfinmonitoring_match_confidence=c.rosfinmonitoring_match_confidence,
            event_count=c.event_count,
            alias_count=c.alias_count,
        )
        for c in result.candidates
    ]


# Research endpoints
def get_research_service() -> ResearchService:
    """Build the research service over the shared session factory."""
    try:
        session_factory = _get_session_factory()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return ResearchService(
        repository=SqlAlchemyPersonResearchRepository(session_factory),
        candidate_query=CandidateQueryService(session_factory),
    )


@app.post("/research", response_model=ResearchResponse)
def research(
    request: ResearchRequest,
    service: ResearchService = Depends(get_research_service),  # noqa: B008
) -> ResearchResponse:
    """Execute a structured, deterministic research request."""
    try:
        return service.execute(request)
    except ResearchCandidatesRequiredError as exc:
        # Structured endpoint: no semantic retrieval here, and ignoring the
        # criterion would return a broader answer than requested.
        raise HTTPException(
            status_code=422,
            detail=(
                "criteria.semantic_query needs semantic retrieval; use POST /research/query "
                "or remove the field"
            ),
        ) from exc
    except ResearchSnapshotNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# Natural-language research endpoints
class ResearchQueryBody(BaseModel):
    """Natural-language research query."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    query: str = Field(min_length=1, max_length=2000)


# A failed workflow is never reported as an empty 200 result.
_WORKFLOW_ERROR_HTTP_STATUS: dict[WorkflowErrorCode, int] = {
    WorkflowErrorCode.LLM_NOT_CONFIGURED: 503,
    WorkflowErrorCode.LLM_TIMEOUT: 504,
    WorkflowErrorCode.LLM_UNAVAILABLE: 503,
    WorkflowErrorCode.LLM_RATE_LIMITED: 503,
    WorkflowErrorCode.LLM_AUTHENTICATION_FAILED: 502,
    WorkflowErrorCode.LLM_REQUEST_REJECTED: 502,
    WorkflowErrorCode.LLM_INVALID_OUTPUT: 502,
    WorkflowErrorCode.NO_ROSFINMONITORING_SNAPSHOT: 409,
    WorkflowErrorCode.SEMANTIC_RETRIEVAL_NOT_CONFIGURED: 503,
    WorkflowErrorCode.SEMANTIC_RETRIEVAL_UNAVAILABLE: 503,
    WorkflowErrorCode.WORKFLOW_UNEXPECTED_ERROR: 500,
}


@lru_cache(maxsize=1)
def _get_research_graph() -> ResearchGraph:
    return create_research_graph(_get_session_factory())


def get_research_query_graph() -> ResearchGraph:
    """Build (once) the LangGraph research workflow."""
    try:
        return _get_research_graph()
    except (RuntimeError, LlmConfigurationError, SemanticConfigurationError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post(
    "/research/query",
    response_model=ResearchQueryResult,
    responses={
        409: {"model": ResearchQueryResult},
        500: {"model": ResearchQueryResult},
        502: {"model": ResearchQueryResult},
        503: {"model": ResearchQueryResult},
        504: {"model": ResearchQueryResult},
    },
)
def research_query(
    body: ResearchQueryBody,
    graph: ResearchGraph = Depends(get_research_query_graph),  # noqa: B008
) -> ResearchQueryResult | JSONResponse:
    """Run a natural-language query through the LangGraph research workflow."""
    result = run_research_query(graph, body.query)
    if result.status is WorkflowStatus.FAILED:
        status_code = (
            _WORKFLOW_ERROR_HTTP_STATUS.get(result.error.code, 502) if result.error else 502
        )
        return JSONResponse(status_code=status_code, content=result.model_dump(mode="json"))
    return result


@app.post(
    "/research/reviews",
    response_model=ResearchReviewTask,
    status_code=201,
    responses={200: {"model": ResearchReviewTask}, 404: {}, 409: {}},
)
def create_research_review(
    body: ResearchReviewTaskRequest,
    db: Session = Depends(get_db),  # noqa: B008
) -> ResearchReviewTask | JSONResponse:
    """Explicitly create a persistent review task for a research result.

    Idempotent: 201 when created, 200 with the existing pending review
    otherwise. The review condition is re-checked against current data.
    """
    try:
        task = ResearchReviewTaskService().create(db, body)
    except ResearchReviewSubjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ResearchReviewConditionNotMetError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    db.commit()
    if not task.created:
        return JSONResponse(status_code=200, content=task.model_dump(mode="json"))
    return task


# Rosfinmonitoring endpoints
@app.get(
    "/rosfinmonitoring/snapshots",
    response_model=list[RosfinmonitoringSnapshotResponse],
)
def list_rosfinmonitoring_snapshots(
    limit: int = Query(default=10, ge=1, le=100),
    db: Session = Depends(get_db),  # noqa: B008
) -> list[RosfinmonitoringSnapshotResponse]:
    """List Rosfinmonitoring snapshots."""
    snapshots = db.scalars(
        select(RosfinmonitoringSnapshotRecord)
        .order_by(RosfinmonitoringSnapshotRecord.snapshot_date.desc())
        .limit(limit)
    ).all()

    return [
        RosfinmonitoringSnapshotResponse(
            id=s.id,
            snapshot_date=s.snapshot_date.isoformat(),
            source_url=s.source_url,
            content_hash=s.content_hash,
            entry_count=s.entry_count,
        )
        for s in snapshots
    ]


@app.get(
    "/rosfinmonitoring/snapshots/{snapshot_id}",
    response_model=RosfinmonitoringSnapshotResponse,
)
def get_rosfinmonitoring_snapshot(
    snapshot_id: int,
    db: Session = Depends(get_db),  # noqa: B008
) -> RosfinmonitoringSnapshotResponse:
    """Get a Rosfinmonitoring snapshot by ID."""
    snapshot = db.get(RosfinmonitoringSnapshotRecord, snapshot_id)

    if not snapshot:
        raise HTTPException(status_code=404, detail="Snapshot not found")

    return RosfinmonitoringSnapshotResponse(
        id=snapshot.id,
        snapshot_date=snapshot.snapshot_date.isoformat(),
        source_url=snapshot.source_url,
        content_hash=snapshot.content_hash,
        entry_count=snapshot.entry_count,
    )


@app.get(
    "/rosfinmonitoring/snapshots/{snapshot_id}/entries",
    response_model=list[RosfinmonitoringEntryResponse],
)
def list_rosfinmonitoring_entries(
    snapshot_id: int,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),  # noqa: B008
) -> list[RosfinmonitoringEntryResponse]:
    """List entries in a Rosfinmonitoring snapshot."""
    snapshot = db.get(RosfinmonitoringSnapshotRecord, snapshot_id)
    if not snapshot:
        raise HTTPException(status_code=404, detail="Snapshot not found")

    entries = db.scalars(
        select(RosfinmonitoringEntryRecord)
        .where(RosfinmonitoringEntryRecord.snapshot_id == snapshot_id)
        .order_by(RosfinmonitoringEntryRecord.id)
        .offset(offset)
        .limit(limit)
    ).all()

    return [
        RosfinmonitoringEntryResponse(
            id=e.id,
            snapshot_id=e.snapshot_id,
            full_name=e.full_name,
            normalized_name=e.normalized_name,
            matching_key=e.matching_key,
            birth_date=e.birth_date.isoformat() if e.birth_date else None,
            inclusion_reason=e.inclusion_reason,
        )
        for e in entries
    ]


# Review endpoints
@app.get("/reviews", response_model=list[ReviewResponse])
def list_reviews(
    status: str | None = Query(default=None),
    subject_type: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),  # noqa: B008
) -> list[ReviewResponse]:
    """List reviews."""
    from db.orm_models import ReviewRecordModel

    query = select(ReviewRecordModel).offset(offset).limit(limit)

    if status:
        query = query.where(ReviewRecordModel.decision == status)

    if subject_type:
        query = query.where(ReviewRecordModel.subject_type == subject_type)

    query = query.order_by(ReviewRecordModel.created_at.desc(), ReviewRecordModel.id.desc())

    reviews = db.scalars(query).all()

    return [
        ReviewResponse(
            id=r.id,
            subject_type=r.subject_type,
            subject_id=r.subject_id,
            decision=r.decision,
            confidence=r.confidence,
            reason=r.reason,
            reviewer_note=r.reviewer_note,
            created_at=r.created_at.isoformat(),
            reviewed_at=r.reviewed_at.isoformat() if r.reviewed_at else None,
        )
        for r in reviews
    ]


# Person resolution (ER v2) review endpoints. Not under /persons/{person_id}.
class PersonResolutionReviewDecisionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: ResolutionReviewAction
    person_id: int | None = None
    source_person_id: int | None = None
    note: str | None = Field(default=None, max_length=2000)


def _person_resolution_reviews(db: Session) -> PersonResolutionReviewService:
    session_factory = sessionmaker(bind=db.get_bind())
    return PersonResolutionReviewService(SqlAlchemyPersonPersistence(session_factory))


@app.get("/person-resolution/reviews", response_model=list[ResolutionReviewView])
def list_person_resolution_reviews(
    limit: int = Query(default=50, ge=1, le=500),
    db: Session = Depends(get_db),  # noqa: B008
) -> list[ResolutionReviewView]:
    """Pending ER v2 decisions with their structured candidate comparison."""
    return _person_resolution_reviews(db).list_pending(db, limit=limit)


@app.get(
    "/person-resolution/reviews/{decision_id}",
    response_model=ResolutionReviewView,
    responses={404: {}},
)
def get_person_resolution_review(
    decision_id: int,
    db: Session = Depends(get_db),  # noqa: B008
) -> ResolutionReviewView:
    try:
        return _person_resolution_reviews(db).get(db, decision_id)
    except ResolutionReviewNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post(
    "/person-resolution/reviews/{decision_id}/decision",
    response_model=ResolutionReviewResult,
    responses={404: {}, 409: {}},
)
def apply_person_resolution_review(
    decision_id: int,
    body: PersonResolutionReviewDecisionBody,
    db: Session = Depends(get_db),  # noqa: B008
) -> ResolutionReviewResult:
    """Apply an explicit reviewer action; MERGE_PERSONS is the only merge path."""
    try:
        result = _person_resolution_reviews(db).apply(
            db,
            decision_id,
            body.action,
            person_id=body.person_id,
            source_person_id=body.source_person_id,
            note=body.note,
        )
    except ResolutionReviewNotFoundError as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ResolutionReviewStateError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    db.commit()
    return result


def get_operation_registry() -> OperationRegistry:
    return _OPERATION_REGISTRY


class OperationRunResponse(BaseModel):
    id: int
    operation: str
    title: str
    parameters: dict[str, object]
    status: str
    created_at: str
    started_at: str | None
    finished_at: str | None
    duration_seconds: float | None
    command: list[str]
    return_code: int | None
    stdout: str
    stderr: str
    error: str | None


def _status_counts(db: Session) -> dict[str, object]:
    pending = db.scalar(
        select(func.count())
        .select_from(PersonResolutionDecisionRecord)
        .where(PersonResolutionDecisionRecord.status == "pending_review")
    )
    latest_run = db.scalars(
        select(MonitoringRunRecord).order_by(MonitoringRunRecord.started_at.desc()).limit(1)
    ).first()
    return {
        "articles": db.scalar(select(func.count()).select_from(ParsedArticleRecord)) or 0,
        "persons": db.scalar(select(func.count()).select_from(PersonRecord)) or 0,
        "pending_reviews": pending or 0,
        "latest_run": latest_run.status if latest_run is not None else "нет",
    }


def _page(
    title: str,
    body: str,
    *,
    active: str,
    instruction: str,
    next_action: str,
    db: Session,
    warning: str | None = None,
) -> HTMLResponse:
    counts = _status_counts(db)
    nav = [
        ("review", "ER-ревью", "/ui/person-resolution/reviews"),
        ("candidates", "Кандидаты", "/ui/candidates"),
        ("search", "Поиск", "/ui/search"),
        ("operations", "Операции", "/ui/operations"),
        ("monitoring", "Monitoring", "/ui/monitoring"),
    ]
    links = "\n".join(
        f'<a class="{"active" if key == active else ""}" href="{href}">{label}</a>'
        for key, label, href in nav
    )
    warning_html = f'<p class="warning">{escape(warning)}</p>' if warning else ""
    return HTMLResponse(
        f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(title)}</title>
  <link rel="stylesheet" href="/static/local-ui.css">
</head>
<body>
  <aside>
    <div class="brand">court-monitor</div>
    <nav>{links}</nav>
  </aside>
  <main>
    <section class="status-strip">
      <span>Статьи: <strong>{counts["articles"]}</strong></span>
      <span>Persons: <strong>{counts["persons"]}</strong></span>
      <span>ER pending: <strong>{counts["pending_reviews"]}</strong></span>
      <span>Последний run: <strong>{escape(str(counts["latest_run"]))}</strong></span>
    </section>
    <section class="instruction">
      <h1>{escape(title)}</h1>
      <p>{escape(instruction)}</p>
      <p><strong>Дальше:</strong> {escape(next_action)}</p>
      {warning_html}
    </section>
    {body}
  </main>
</body>
</html>"""
    )


def _fmt(value: object) -> str:
    return "—" if value is None or value == "" else escape(str(value))


def _button(action_url: str, label: str) -> str:
    return (
        f'<form method="post" action="{escape(action_url)}"><button>{escape(label)}</button></form>'
    )


def _review_table(reviews: list[ResolutionReviewView]) -> str:
    rows = "\n".join(
        f"""<tr>
  <td><a href="/ui/person-resolution/reviews/{review.decision_id}">{review.decision_id}</a></td>
  <td>{escape(review.incoming_name)}</td>
  <td>{escape(", ".join(review.reasons))}</td>
  <td>{_fmt(review.decision_margin)}</td>
  <td>{_fmt(review.source.title)}</td>
</tr>"""
        for review in reviews
    )
    return f"""<table>
<thead><tr><th>ID</th><th>Упоминание</th><th>Причины</th><th>Margin</th><th>Источник</th></tr></thead>
<tbody>{rows}</tbody>
</table>"""


@app.get("/ui")
def ui_root() -> RedirectResponse:
    return RedirectResponse("/ui/person-resolution/reviews", status_code=303)


@app.get("/ui/person-resolution/reviews")
def ui_list_person_resolution_reviews(
    limit: int = Query(default=50, ge=1, le=500),
    db: Session = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    reviews = _person_resolution_reviews(db).list_pending(db, limit=limit)
    if not reviews:
        return _page(
            "ER-ревью",
            '<section class="empty">Очередь пуста.</section>',
            active="review",
            instruction="Здесь разбираются pending ER decisions: связать упоминание с Person или создать новую.",
            next_action="Когда появятся pending decisions, откройте первое и примените явное решение.",
            db=db,
        )
    return _page(
        "ER-ревью",
        f"""<section class="toolbar">
<span class="muted">Показано: {len(reviews)}</span>
<p><a class="primary" href="/ui/person-resolution/reviews/{reviews[0].decision_id}">Открыть первое</a></p>
</section>
{_review_table(reviews)}""",
        active="review",
        instruction="Разберите pending ER decisions пачкой: список отсортирован от старых к новым.",
        next_action="Откройте первое решение, сравните кандидатов и примените действие.",
        db=db,
    )


@app.get("/ui/person-resolution/reviews/{decision_id}")
def ui_get_person_resolution_review(
    decision_id: int,
    db: Session = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    try:
        review = _person_resolution_reviews(db).get(db, decision_id)
    except ResolutionReviewNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    candidates = []
    for candidate in review.candidates:
        other = (
            next(
                item.person_id
                for item in review.candidates
                if item.person_id != candidate.person_id
            )
            if len(review.candidates) == 2
            else None
        )
        actions = [
            _button(
                f"/ui/person-resolution/reviews/{review.decision_id}/decision"
                f"?action=link_to_person&person_id={candidate.person_id}",
                "Связать",
            )
        ]
        if other is not None:
            actions.append(
                _button(
                    f"/ui/person-resolution/reviews/{review.decision_id}/decision"
                    f"?action=keep_separate&person_id={candidate.person_id}&source_person_id={other}",
                    "Разные",
                )
            )
            actions.append(
                _button(
                    f"/ui/person-resolution/reviews/{review.decision_id}/decision"
                    f"?action=merge_persons&person_id={candidate.person_id}&source_person_id={other}",
                    "Слить",
                )
            )
        candidates.append(
            f"""<tr>
  <td><a href="/ui/persons/{candidate.person_id}">{candidate.person_id}</a></td>
  <td>{escape(candidate.canonical_name)}</td>
  <td>{_fmt(candidate.resolution_score)}</td>
  <td>{escape(candidate.surname.match)} / {escape(candidate.given_name.match)} / {escape(candidate.patronymic.match)}</td>
  <td>{escape(", ".join(candidate.aliases))}</td>
  <td>{escape(", ".join(candidate.conflicts))}</td>
  <td class="actions">{"".join(actions)}</td>
</tr>"""
        )
    source = ""
    if review.source.article_id is not None:
        source = (
            f'<p>Источник: <a href="/ui/articles/{review.source.article_id}">'
            f"{_fmt(review.source.title)}</a></p>"
        )
    return _page(
        f"ER-ревью {decision_id}",
        f"""<section class="split">
<div>
<section class="band">
  <h2>{escape(review.incoming_name)}</h2>
  <dl>
    <dt>surface</dt><dd>{_fmt(review.surface_text)}</dd>
    <dt>normalized</dt><dd>{_fmt(review.normalized_form)}</dd>
    <dt>reasons</dt><dd>{escape(", ".join(review.reasons))}</dd>
    <dt>margin</dt><dd>{_fmt(review.decision_margin)}</dd>
  </dl>
  {source}
  {_button(f"/ui/person-resolution/reviews/{review.decision_id}/decision?action=create_new_person", "Создать новую персону")}
</section>
<table>
  <thead><tr><th>ID</th><th>Персона</th><th>Score</th><th>ФИО</th><th>Алиасы</th><th>Конфликты</th><th></th></tr></thead>
  <tbody>{"".join(candidates)}</tbody>
</table>
</div>
<aside class="side-panel">
  <h2>Быстрые действия</h2>
  <p>После применения откроется следующий pending item.</p>
  <a class="secondary" href="/ui/person-resolution/reviews">К списку</a>
</aside>
</section>""",
        active="review",
        instruction="Сравните входящее упоминание с кандидатами ER и выберите ручное решение.",
        next_action="Проверьте source/evidence и нажмите безопасное действие в строке кандидата.",
        db=db,
    )


@app.post("/ui/person-resolution/reviews/{decision_id}/decision", response_model=None)
def ui_apply_person_resolution_review(
    decision_id: int,
    action: ResolutionReviewAction,
    person_id: int | None = None,
    source_person_id: int | None = None,
    db: Session = Depends(get_db),  # noqa: B008
) -> RedirectResponse | HTMLResponse:
    service = _person_resolution_reviews(db)
    try:
        service.apply(
            db,
            decision_id,
            action,
            person_id=person_id,
            source_person_id=source_person_id,
        )
    except ResolutionReviewNotFoundError as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ResolutionReviewStateError as exc:
        db.rollback()
        return _page(
            "ER-ревью",
            f'<section class="band"><h2>Не применено</h2><p>{escape(str(exc))}</p></section>',
            active="review",
            instruction="Действие ревью не применилось: состояние данных изменилось или параметры неполные.",
            next_action="Вернитесь к решению, перечитайте кандидатов и выберите действие заново.",
            db=db,
            warning="База могла измениться между открытием страницы и нажатием кнопки.",
        )
    db.commit()
    next_reviews = service.list_pending(db, limit=1)
    if not next_reviews:
        return RedirectResponse("/ui/person-resolution/reviews", status_code=303)
    return RedirectResponse(
        f"/ui/person-resolution/reviews/{next_reviews[0].decision_id}", status_code=303
    )


@app.get("/ui/persons/{person_id}")
def ui_get_person(
    person_id: int,
    db: Session = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    detail = get_person_detail(person_id, db)
    aliases = "".join(
        f"<li>{escape(alias.surface_text)} <span>{escape(alias.origin)}</span></li>"
        for alias in detail.aliases
    )
    events = "".join(
        f"""<tr>
  <td>{event.id}</td>
  <td>{escape(event.event_type)}</td>
  <td>{_fmt(event.event_date)}</td>
  <td>{escape(event.role)}</td>
  <td><a href="/ui/articles/{event.evidence.article_id}?start={event.evidence.start_offset}&end={event.evidence.end_offset}">{escape(event.evidence.title)}</a></td>
  <td>{escape(event.evidence.text)}</td>
</tr>"""
        for event in detail.events
    )
    persecution = detail.persecution.status if detail.persecution else "—"
    rosfin = detail.rosfinmonitoring.status if detail.rosfinmonitoring else "—"
    return _page(
        detail.person.canonical_name,
        f"""<section class="band">
  <dl>
    <dt>ID</dt><dd>{detail.person.id}</dd>
    <dt>Статус</dt><dd>{escape(detail.person.status)}</dd>
    <dt>Persecution</dt><dd>{escape(persecution)}</dd>
    <dt>Росфинмониторинг</dt><dd>{escape(rosfin)}</dd>
  </dl>
</section>
<h2>Алиасы</h2>
<ul>{aliases}</ul>
<h2>События</h2>
<table>
  <thead><tr><th>ID</th><th>Тип</th><th>Дата</th><th>Роль</th><th>Статья</th><th>Span</th></tr></thead>
  <tbody>{events}</tbody>
</table>""",
        active="search",
        instruction="Карточка Person показывает только проверяемые факты с переходом к source span.",
        next_action="Откройте статью в строке события и проверьте подсвеченный evidence span.",
        db=db,
    )


@app.get("/ui/articles/{article_id}")
def ui_get_article(
    article_id: int,
    start: int | None = Query(default=None, ge=0),
    end: int | None = Query(default=None, ge=0),
    db: Session = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    article = _article_response(db, article_id)
    text = article.text
    if start is not None and end is not None and start <= end <= len(text):
        rendered = (
            escape(text[:start]) + f"<mark>{escape(text[start:end])}</mark>" + escape(text[end:])
        )
    else:
        rendered = escape(text)
    return _page(
        article.title,
        f"""<p><a href="{escape(article.url)}">{escape(article.url)}</a></p>
<article>{rendered}</article>""",
        active="search",
        instruction="Это полный ParsedArticle.text — source of truth для evidence.",
        next_action="Проверьте подсвеченный span или вернитесь к карточке Person.",
        db=db,
    )


@app.get("/ui/search")
def ui_search(
    query: str | None = Query(default=None, max_length=500),
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    results: list[SearchHit] = []
    if query and query.strip():
        results = search_articles(query, limit, db)
    rows = "\n".join(
        f"""<tr>
  <td><a href="/ui/articles/{hit.article_id}">{escape(hit.title)}</a></td>
  <td>{_fmt(hit.published_at)}</td>
  <td>{_fmt(hit.score)}</td>
</tr>"""
        for hit in results
    )
    return _page(
        "Поиск",
        f"""<form method="get" class="search">
  <input name="query" value="{escape(query or "")}" autofocus>
  <button>Искать</button>
</form>
<table><thead><tr><th>Статья</th><th>Дата</th><th>Score</th></tr></thead><tbody>{rows}</tbody></table>""",
        active="search",
        instruction="Lexical search ищет по ParsedArticle.text через PostgreSQL russian tsvector.",
        next_action="Введите фразу, откройте статью и используйте её как provenance, не как финальный результат.",
        db=db,
    )


@app.get("/ui/candidates")
def ui_candidates(
    snapshot_id: int | None = Query(default=None, ge=1),
    min_confidence: float = Query(default=0.7, ge=0.0, le=1.0),
    limit: int = Query(default=100, ge=1, le=1000),
    db: Session = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    snapshots = list_rosfinmonitoring_snapshots(limit=20, db=db)
    selected_snapshot_id = snapshot_id or (snapshots[0].id if snapshots else None)
    if selected_snapshot_id is None:
        return _page(
            "Кандидаты",
            '<p class="muted">Snapshot Росфинмониторинга ещё не загружен.</p>',
            active="candidates",
            instruction="Здесь люди с политической классификацией и подтверждённым отсутствием в выбранном snapshot РФМ.",
            next_action="Импортируйте snapshot Росфинмониторинга через CLI, затем вернитесь сюда.",
            db=db,
            warning="Без snapshot нельзя отличить подтверждённое отсутствие от отсутствия проверки.",
        )

    try:
        candidates = list_candidates(
            snapshot_id=selected_snapshot_id,
            min_persecution_confidence=min_confidence,
            limit=limit,
            db=db,
        )
    except HTTPException as exc:
        if exc.status_code != 404:
            raise
        candidates = []

    snapshot_options = "".join(
        f'<option value="{item.id}" {"selected" if item.id == selected_snapshot_id else ""}>'
        f"{item.id} — {escape(item.snapshot_date)} ({item.entry_count:,})</option>"
        for item in snapshots
    )
    rows = "".join(
        f"""<tr>
  <td>{position}</td>
  <td><a href="/ui/persons/{candidate.person_id}">{candidate.person_id}</a></td>
  <td><a href="/ui/persons/{candidate.person_id}">{escape(candidate.canonical_name)}</a></td>
  <td>{candidate.persecution_confidence:.2f}</td>
  <td>{candidate.event_count}</td>
  <td>{escape(candidate.rosfinmonitoring_status)}</td>
  <td>{escape(", ".join(candidate.persecution_reasons))}</td>
</tr>"""
        for position, candidate in enumerate(candidates, start=1)
    )
    return _page(
        "Кандидаты",
        f"""<form method="get" class="toolbar">
  <label>Snapshot РФМ <select name="snapshot_id">{snapshot_options}</select></label>
  <label>Min confidence <input type="number" name="min_confidence" min="0" max="1" step="0.05" value="{min_confidence}"></label>
  <label>Limit <input type="number" name="limit" min="1" max="1000" value="{limit}"></label>
  <button>Обновить</button>
  <a class="secondary" href="/ui/candidates/export?{urlencode({"snapshot_id": selected_snapshot_id, "min_confidence": min_confidence, "limit": limit})}">Скачать CSV</a>
  <a class="secondary" href="/ui/candidates/export.pdf?{urlencode({"snapshot_id": selected_snapshot_id, "min_confidence": min_confidence, "limit": limit})}">Скачать PDF</a>
</form>
<p class="muted">Найдено: {len(candidates)}. Статус РФМ: <code>not_matched</code>.</p>
<table><thead><tr><th>№</th><th>Person ID</th><th>Персона</th><th>Political confidence</th><th>Events</th><th>RF status</th><th>Причины</th></tr></thead><tbody>{rows}</tbody></table>""",
        active="candidates",
        instruction="Кандидаты — политически классифицированные люди с подтверждённым статусом РФМ not_matched.",
        next_action="Откройте Person, проверьте события и evidence spans в исходных статьях.",
        db=db,
    )


@app.get("/ui/candidates/export")
def ui_candidates_export(
    snapshot_id: int = Query(..., ge=1),
    min_confidence: float = Query(default=0.7, ge=0.0, le=1.0),
    limit: int = Query(default=100, ge=1, le=1000),
    db: Session = Depends(get_db),  # noqa: B008
) -> Response:
    candidates = list_candidates(
        snapshot_id=snapshot_id,
        min_persecution_confidence=min_confidence,
        limit=limit,
        db=db,
    )
    output = StringIO(newline="")
    csv_writer = writer(output)
    csv_writer.writerow(
        [
            "person_id",
            "canonical_name",
            "persecution_confidence",
            "persecution_reasons",
            "event_count",
            "rosfinmonitoring_status",
            "rosfinmonitoring_match_confidence",
        ]
    )
    for candidate in candidates:
        csv_writer.writerow(
            [
                candidate.person_id,
                candidate.canonical_name,
                candidate.persecution_confidence,
                "; ".join(candidate.persecution_reasons),
                candidate.event_count,
                candidate.rosfinmonitoring_status,
                candidate.rosfinmonitoring_match_confidence,
            ]
        )
    return Response(
        content="\ufeff" + output.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="political-candidates-{snapshot_id}.csv"'
        },
    )


def _candidate_pdf_font() -> tuple[str, str]:
    regular_paths = (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/TTF/DejaVuSans.ttf",
    )
    bold_paths = (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    )
    regular = next((Path(path) for path in regular_paths if Path(path).exists()), None)
    bold = next((Path(path) for path in bold_paths if Path(path).exists()), None)
    if regular is None or bold is None:
        raise HTTPException(status_code=503, detail="Cyrillic PDF font is not installed")
    if "CandidateSans" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont("CandidateSans", str(regular)))
    if "CandidateSans-Bold" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont("CandidateSans-Bold", str(bold)))
    return "CandidateSans", "CandidateSans-Bold"


@app.get("/ui/candidates/export.pdf")
def ui_candidates_export_pdf(
    snapshot_id: int = Query(..., ge=1),
    min_confidence: float = Query(default=0.7, ge=0.0, le=1.0),
    limit: int = Query(default=100, ge=1, le=1000),
    db: Session = Depends(get_db),  # noqa: B008
) -> Response:
    candidates = list_candidates(
        snapshot_id=snapshot_id,
        min_persecution_confidence=min_confidence,
        limit=limit,
        db=db,
    )
    regular_font, bold_font = _candidate_pdf_font()
    buffer = BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        rightMargin=10 * mm,
        leftMargin=10 * mm,
        topMargin=10 * mm,
        bottomMargin=10 * mm,
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "CandidatePdfTitle", parent=styles["Title"], fontName=bold_font, fontSize=14, leading=18
    )
    cell_style = ParagraphStyle(
        "CandidatePdfCell", parent=styles["BodyText"], fontName=regular_font, fontSize=7, leading=9
    )
    header_style = ParagraphStyle(
        "CandidatePdfHeader", parent=cell_style, fontName=bold_font, textColor=colors.white
    )

    def cell(value: object, *, header: bool = False) -> Paragraph:
        return Paragraph(escape(str(value)), header_style if header else cell_style)

    headers = [
        "№",
        "ID",
        "Персона",
        "Political confidence",
        "Причины",
        "Events",
        "RF status",
        "RF match",
    ]
    data = [[cell(header, header=True) for header in headers]]
    data.extend(
        [
            cell(position),
            cell(candidate.person_id),
            cell(candidate.canonical_name),
            cell(f"{candidate.persecution_confidence:.2f}"),
            cell("; ".join(candidate.persecution_reasons)),
            cell(candidate.event_count),
            cell(candidate.rosfinmonitoring_status),
            cell(candidate.rosfinmonitoring_match_confidence or "—"),
        ]
        for position, candidate in enumerate(candidates, start=1)
    )
    table = Table(
        data,
        repeatRows=1,
        colWidths=[10 * mm, 18 * mm, 42 * mm, 25 * mm, 78 * mm, 15 * mm, 25 * mm, 22 * mm],
    )
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#243447")),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#b7c2cc")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#eef3f7")]),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    story = [
        Paragraph("Политические кандидаты вне списка Росфинмониторинга", title_style),
        Paragraph(
            f"Snapshot: {snapshot_id}; minimum confidence: {min_confidence:.2f}; найдено: {len(candidates)}",
            cell_style,
        ),
        Spacer(1, 6 * mm),
        table,
    ]
    document.build(story)
    return Response(
        content=buffer.getvalue(),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="political-candidates-{snapshot_id}.pdf"'
        },
    )


def _operation_parameters_from_query(
    source: str | None,
    limit: int | None,
    workers: int | None,
) -> OperationParameters:
    return OperationParameters(source=source or None, limit=limit, workers=workers)


def _operation_form(name: str, params: OperationParameters) -> str:
    source_options = "".join(
        f'<option value="{escape(source)}" {"selected" if source == params.source else ""}>{escape(source)}</option>'
        for source in sorted(SOURCES)
    )
    source_field = (
        f"""<label>Источник
  <select name="source">{source_options}</select>
</label>"""
        if name in {"discover-and-ingest", "extract-entities"}
        else ""
    )
    workers_field = (
        f"""<label>Workers
  <input type="number" name="workers" min="1" max="32" value="{params.workers or 1}">
</label>"""
        if name == "resolve-people"
        else ""
    )
    return f"""<form method="get" class="operation-form">
  {source_field}
  <label>Limit
    <input type="number" name="limit" min="1" max="100000" value="{params.limit or 100}">
  </label>
  {workers_field}
  <button>Preview</button>
</form>"""


def _operation_query(params: OperationParameters) -> str:
    values = {key: value for key, value in params.model_dump().items() if value is not None}
    return urlencode(values)


def _run_badge(status: str) -> str:
    return f'<span class="badge {escape(status)}">{escape(status)}</span>'


@app.get("/operations/runs", response_model=list[OperationRunResponse])
def list_operation_runs(
    registry: OperationRegistry = Depends(get_operation_registry),  # noqa: B008
) -> list[OperationRunResponse]:
    return [OperationRunResponse(**operation_run_to_dict(run)) for run in registry.list_runs()]


@app.get("/operations/runs/{run_id}", response_model=OperationRunResponse)
def get_operation_run(
    run_id: int,
    registry: OperationRegistry = Depends(get_operation_registry),  # noqa: B008
) -> OperationRunResponse:
    try:
        return OperationRunResponse(**operation_run_to_dict(registry.get(run_id)))
    except OperationNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Operation run not found") from exc


@app.get("/ui/operations")
def ui_operations(
    db: Session = Depends(get_db),  # noqa: B008
    registry: OperationRegistry = Depends(get_operation_registry),  # noqa: B008
) -> HTMLResponse:
    cards = []
    for definition in registry.definitions():
        cards.append(
            f"""<section class="operation-card">
  <h2>{escape(definition.title)}</h2>
  <p>{escape(definition.description)}</p>
  <p class="muted">{escape(definition.next_action)}</p>
  <a class="primary" href="/ui/operations/{definition.name}">Настроить</a>
</section>"""
        )
    runs = "".join(
        f"""<tr>
  <td><a href="/ui/operations/runs/{run.id}">{run.id}</a></td>
  <td>{escape(run.operation.title)}</td>
  <td>{_run_badge(run.status.value)}</td>
  <td>{_fmt(run.started_at)}</td>
  <td>{_fmt(run.duration_seconds)}</td>
</tr>"""
        for run in registry.list_runs()[:20]
    )
    return _page(
        "Операции",
        f"""<section class="operation-grid">{"".join(cards)}</section>
<h2>Последние runs</h2>
<table><thead><tr><th>ID</th><th>Операция</th><th>Status</th><th>Started</th><th>Duration</th></tr></thead><tbody>{runs}</tbody></table>""",
        active="operations",
        instruction="Здесь запускаются routine pipeline operations на живой базе.",
        next_action="Выберите операцию, проверьте preview и подтвердите run.",
        db=db,
        warning="Все операции на этой странице могут менять данные или занимать долгое время.",
    )


@app.get("/ui/operations/{name}")
def ui_operation_preview(
    name: str,
    source: str | None = None,
    limit: int | None = Query(default=None, ge=1, le=100_000),
    workers: int | None = Query(default=None, ge=1, le=32),
    db: Session = Depends(get_db),  # noqa: B008
    registry: OperationRegistry = Depends(get_operation_registry),  # noqa: B008
) -> HTMLResponse:
    try:
        definition = registry.definition(name)
        params = registry.prepare_parameters(
            definition, _operation_parameters_from_query(source, limit, workers)
        )
    except (OperationNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    try:
        command = OPERATION_DEFINITIONS[name].name
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Operation not found") from exc
    confirm_query = _operation_query(params)
    return _page(
        definition.title,
        f"""{_operation_form(name, params)}
<section class="band">
  <h2>Preview</h2>
  <dl>
    <dt>Operation</dt><dd>{escape(command)}</dd>
    <dt>Source</dt><dd>{_fmt(params.source)}</dd>
    <dt>Limit</dt><dd>{_fmt(params.limit)}</dd>
    <dt>Workers</dt><dd>{_fmt(params.workers)}</dd>
  </dl>
  <form method="post" action="/ui/operations/{escape(name)}/confirm?{escape(confirm_query)}">
    <button>Подтвердить run</button>
  </form>
</section>""",
        active="operations",
        instruction=definition.description,
        next_action=definition.next_action,
        db=db,
        warning=definition.warning,
    )


@app.post("/ui/operations/{name}/confirm", response_model=None)
def ui_operation_confirm(
    name: str,
    source: str | None = None,
    limit: int | None = Query(default=None, ge=1, le=100_000),
    workers: int | None = Query(default=None, ge=1, le=32),
    registry: OperationRegistry = Depends(get_operation_registry),  # noqa: B008
) -> RedirectResponse:
    try:
        run = registry.start(name, _operation_parameters_from_query(source, limit, workers))
    except OperationNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Operation not found") from exc
    except (OperationConflictError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return RedirectResponse(f"/ui/operations/runs/{run.id}", status_code=303)


@app.get("/ui/operations/runs/{run_id}")
def ui_operation_run(
    run_id: int,
    db: Session = Depends(get_db),  # noqa: B008
    registry: OperationRegistry = Depends(get_operation_registry),  # noqa: B008
) -> HTMLResponse:
    try:
        run = registry.get(run_id)
    except OperationNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Operation run not found") from exc
    output = escape(run.stdout or run.stderr or run.error or "Пока нет вывода.")
    refresh = (
        '<meta http-equiv="refresh" content="2">'
        if run.status.value in {"pending", "running"}
        else ""
    )
    body = f"""{refresh}
<section class="band">
  <dl>
    <dt>Status</dt><dd>{_run_badge(run.status.value)}</dd>
    <dt>Operation</dt><dd>{escape(run.operation.title)}</dd>
    <dt>Started</dt><dd>{_fmt(run.started_at)}</dd>
    <dt>Finished</dt><dd>{_fmt(run.finished_at)}</dd>
    <dt>Duration</dt><dd>{_fmt(run.duration_seconds)}</dd>
    <dt>Return code</dt><dd>{_fmt(run.return_code)}</dd>
  </dl>
</section>
<h2>Command</h2>
<pre>{" ".join(escape(part) for part in run.command)}</pre>
<h2>Output</h2>
<pre>{output}</pre>"""
    return _page(
        f"Run #{run.id}",
        body,
        active="operations",
        instruction="Run detail показывает состояние и последние строки вывода операции.",
        next_action="Дождитесь завершения, затем проверьте counts/status или откройте новую операцию.",
        db=db,
        warning="При reload running run не перезапускается: страница только перечитывает состояние.",
    )


@app.get("/ui/monitoring")
def ui_monitoring(
    db: Session = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    runs = list_monitoring_runs(limit=20, offset=0, db=db)
    findings = list_monitoring_findings(active_only=True, limit=20, offset=0, db=db)
    run_rows = "".join(
        f"""<tr>
  <td><a href="/monitoring/runs/{run.id}">{run.id}</a></td>
  <td>{_fmt(run.scope)}</td>
  <td>{escape(run.status.value)}</td>
  <td>{_fmt(run.started_at)}</td>
  <td>{_fmt(run.duration_seconds)}</td>
</tr>"""
        for run in runs
    )
    finding_rows = "".join(
        f"""<tr>
  <td>{finding.id}</td>
  <td><a href="/ui/persons/{finding.person_id}">{finding.person_id}</a></td>
  <td>{escape(finding.finding_type)}</td>
  <td>{_fmt(finding.last_seen_at)}</td>
</tr>"""
        for finding in findings
    )
    return _page(
        "Monitoring",
        f"""<h2>Последние runs</h2>
<table><thead><tr><th>ID</th><th>Scope</th><th>Status</th><th>Started</th><th>Duration</th></tr></thead><tbody>{run_rows}</tbody></table>
<h2>Active findings</h2>
<table><thead><tr><th>ID</th><th>Person</th><th>Type</th><th>Last seen</th></tr></thead><tbody>{finding_rows}</tbody></table>""",
        active="monitoring",
        instruction="Здесь видно свежесть monitoring runs и actionable findings.",
        next_action="Если данные устарели, запустите нужную операцию на странице Operations.",
        db=db,
    )


def _monitoring_repository(db: Session) -> SqlAlchemyMonitoringRepository:
    return SqlAlchemyMonitoringRepository(sessionmaker(bind=db.get_bind()))


@app.get("/monitoring/status", response_model=MonitoringStatusView)
def get_monitoring_status(
    db: Session = Depends(get_db),  # noqa: B008
) -> MonitoringStatusView:
    """Running and latest monitoring runs, source checkpoints, active findings."""
    return _monitoring_repository(db).status()


@app.get("/monitoring/runs", response_model=list[MonitoringRunView])
def list_monitoring_runs(
    limit: int = Query(default=20, ge=1, le=200),
    source: str | None = None,
    status: MonitoringRunStatus | None = None,
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),  # noqa: B008
) -> list[MonitoringRunView]:
    return _monitoring_repository(db).list_runs(
        limit=limit, offset=offset, source=source, status=status
    )


@app.get(
    "/monitoring/runs/{run_id}",
    response_model=MonitoringRunDetails,
    responses={404: {}},
)
def get_monitoring_run(
    run_id: int,
    db: Session = Depends(get_db),  # noqa: B008
) -> MonitoringRunDetails:
    """One run with its counters, stage metrics and failed items."""
    details = _monitoring_repository(db).get_run_details(run_id)
    if details is None:
        raise HTTPException(status_code=404, detail=f"Monitoring run {run_id} not found")
    return details


@app.get("/monitoring/findings", response_model=list[MonitoringFindingView])
def list_monitoring_findings(
    active_only: bool = True,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),  # noqa: B008
) -> list[MonitoringFindingView]:
    return MonitoringFindingService(sessionmaker(bind=db.get_bind())).list_findings(
        active_only=active_only, limit=limit, offset=offset
    )


# Health endpoints (ADR 0014)
@app.get("/health")
def health_check() -> dict[str, str]:
    """Liveness (kept for compatibility; prefer /health/live)."""
    return {"status": "ok"}


@app.get("/health/live", response_model=LivenessReport)
def health_live() -> LivenessReport:
    """The process answers. Never checks dependencies."""
    return LivenessReport()


def get_readiness_checker() -> ReadinessChecker:
    try:
        session_factory: sessionmaker[Session] | None = _get_session_factory()
    except RuntimeError:
        session_factory = None
    semantic = SemanticRetrievalConfig.from_env()
    return ReadinessChecker(
        session_factory,
        expected_revision=expected_schema_revision(),
        qdrant_probe=None if semantic.qdrant_url is None else qdrant_probe(semantic.qdrant_url),
        together_configured=bool(
            os.getenv("TOGETHER_API_KEY", "").strip() and os.getenv("TOGETHER_MODEL", "").strip()
        ),
        stale_run_after=MonitoringSettings.from_env().stale_run_after,
    )


@app.get(
    "/health/ready",
    response_model=ReadinessReport,
    responses={503: {"model": ReadinessReport}},
)
def health_ready(
    checker: ReadinessChecker = Depends(get_readiness_checker),  # noqa: B008
) -> ReadinessReport | JSONResponse:
    """Database and schema are required; Qdrant, Together AI and monitoring are reported."""
    report = checker.check()
    if report.status is ReadinessStatus.UNAVAILABLE:
        return JSONResponse(status_code=503, content=report.model_dump(mode="json"))
    return report
