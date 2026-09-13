"""FastAPI application for court-monitor read-only API."""

import os
from collections.abc import Iterator
from functools import lru_cache

from fastapi import Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from candidate_query_service import CandidateQueryService
from database import create_database_engine, create_session_factory
from orm_models import (
    PersecutionClassificationRecord,
    PersonAliasRecord,
    PersonRecord,
    RosfinmonitoringEntryRecord,
    RosfinmonitoringSnapshotRecord,
)

# Create FastAPI app
app = FastAPI(
    title="Court Monitor API",
    description="Read-only API for court-monitor data",
    version="1.0.0",
)


# Database dependency
@lru_cache(maxsize=1)
def _get_session_factory() -> sessionmaker[Session]:
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL environment variable is not set")

    engine = create_database_engine(database_url)
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
    query = select(PersonRecord).offset(offset).limit(limit)

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
            PersecutionClassificationRecord.person_id == person_id
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
    db: Session = Depends(get_db),  # noqa: B008
) -> list[ReviewResponse]:
    """List reviews."""
    from orm_models import ReviewRecordModel

    query = select(ReviewRecordModel).limit(limit)

    if status:
        query = query.where(ReviewRecordModel.decision == status)

    if subject_type:
        query = query.where(ReviewRecordModel.subject_type == subject_type)

    query = query.order_by(ReviewRecordModel.created_at.desc())

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


# Health check endpoint
@app.get("/health")
def health_check() -> dict[str, str]:
    """Health check endpoint."""
    return {"status": "ok"}
