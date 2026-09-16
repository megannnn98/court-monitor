"""FastAPI application for court-monitor read-only API."""

import logging
import os
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import asynccontextmanager
from functools import lru_cache
from html import escape

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from candidates.service import CandidateQueryService
from db.database import DatabasePoolSettings, create_database_engine, create_session_factory
from db.orm_models import (
    ArticleExtractionRunRecord,
    ExtractedEventRecord,
    ParsedArticleRecord,
    PersecutionClassificationRecord,
    PersonAliasRecord,
    PersonEventLinkRecord,
    PersonRecord,
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

logger = logging.getLogger("api")


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


def _page(title: str, body: str) -> HTMLResponse:
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
  <header>
    <a href="/ui/person-resolution/reviews">ER-ревью</a>
    <a href="/ui/search">Поиск</a>
    <a href="/candidates?snapshot_id=1">Кандидаты JSON</a>
  </header>
  <main>{body}</main>
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
        return _page("ER-ревью", "<h1>ER-ревью</h1><p>Очередь пуста.</p>")
    return _page(
        "ER-ревью",
        f"""<h1>ER-ревью</h1>
<p class="muted">Показано: {len(reviews)}</p>
<p><a class="primary" href="/ui/person-resolution/reviews/{reviews[0].decision_id}">Открыть первое</a></p>
{_review_table(reviews)}""",
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
        f"""<h1>ER-ревью #{review.decision_id}</h1>
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
</table>""",
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
        return _page("ER-ревью", f"<h1>Не применено</h1><p>{escape(str(exc))}</p>")
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
        f"""<h1>{escape(detail.person.canonical_name)}</h1>
<section class="band">
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
        f"""<h1>{escape(article.title)}</h1>
<p><a href="{escape(article.url)}">{escape(article.url)}</a></p>
<article>{rendered}</article>""",
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
        f"""<h1>Поиск</h1>
<form method="get" class="search">
  <input name="query" value="{escape(query or "")}" autofocus>
  <button>Искать</button>
</form>
<table><thead><tr><th>Статья</th><th>Дата</th><th>Score</th></tr></thead><tbody>{rows}</tbody></table>""",
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
