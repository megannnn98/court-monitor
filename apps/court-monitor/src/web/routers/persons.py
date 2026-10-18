"""Persons: canonical records, aliases, classification, events."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from analysis import read as analysis_read
from persons import read as person_read
from web.dependencies import get_db
from web.response_models import (
    EvidenceSpanResponse,
    LatestRosfinMatchResponse,
    PersecutionClassificationResponse,
    PersonAliasResponse,
    PersonDetailResponse,
    PersonEventResponse,
    PersonResponse,
)

router = APIRouter()


# Person endpoints
@router.get("/persons", response_model=list[PersonResponse])
def list_persons(
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    status: str | None = Query(default=None),
    db: Session = Depends(get_db),  # noqa: B008
) -> list[PersonResponse]:
    """List all persons."""
    # Ordered: offset pagination must not skip or repeat rows between pages.
    persons = person_read.browse_persons(db, status=status, limit=limit, offset=offset)

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


@router.get("/persons/{person_id}", response_model=PersonResponse)
def get_person(
    person_id: int,
    db: Session = Depends(get_db),  # noqa: B008
) -> PersonResponse:
    """Get a person by ID."""
    person = person_read.person(db, person_id)

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


@router.get("/persons/{person_id}/aliases", response_model=list[PersonAliasResponse])
def get_person_aliases(
    person_id: int,
    db: Session = Depends(get_db),  # noqa: B008
) -> list[PersonAliasResponse]:
    """Get all aliases for a person."""
    person = person_read.person(db, person_id)
    if not person:
        raise HTTPException(status_code=404, detail="Person not found")

    aliases = person_read.aliases(db, person_id, ordered=False)

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


@router.get(
    "/persons/{person_id}/persecution",
    response_model=PersecutionClassificationResponse | None,
)
def get_person_persecution(
    person_id: int,
    db: Session = Depends(get_db),  # noqa: B008
) -> PersecutionClassificationResponse | None:
    """Get persecution classification for a person."""
    person = person_read.person(db, person_id)
    if not person:
        raise HTTPException(status_code=404, detail="Person not found")

    classification = analysis_read.person_classification(db, person_id)

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


def _person_response(person: person_read.PersonSummary) -> PersonResponse:
    return PersonResponse(
        id=person.id,
        canonical_name=person.canonical_name,
        normalized_name=person.normalized_name,
        matching_key=person.matching_key,
        status=person.status,
        merged_into_id=person.merged_into_id,
    )


def _alias_response(alias: person_read.Alias) -> PersonAliasResponse:
    return PersonAliasResponse(
        id=alias.id,
        person_id=alias.person_id,
        surface_text=alias.surface_text,
        normalized_text=alias.normalized_text,
        matching_key=alias.matching_key,
        origin=alias.origin,
        confidence=alias.confidence,
    )


def _latest_persecution_response(
    db: Session, person_id: int
) -> PersecutionClassificationResponse | None:
    classification = analysis_read.person_classification(db, person_id)
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
    match = analysis_read.person_list_match(db, person_id)
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


@router.get("/persons/{person_id}/events", response_model=list[PersonEventResponse])
def get_person_events(
    person_id: int,
    db: Session = Depends(get_db),  # noqa: B008
) -> list[PersonEventResponse]:
    """Events linked to a Person, each with its source article span."""
    person = person_read.person(db, person_id)
    if person is None:
        raise HTTPException(status_code=404, detail="Person not found")

    return [
        PersonEventResponse(
            id=event.id,
            event_type=event.event_type,
            event_date=event.event_date.isoformat() if event.event_date else None,
            role=event.role,
            confidence=event.confidence,
            evidence=EvidenceSpanResponse(
                article_id=event.article_id,
                source_name=event.source_name,
                title=event.title,
                url=event.url,
                start_offset=event.start_offset,
                end_offset=event.end_offset,
                text=event.text,
            ),
        )
        for event in person_read.event_evidence(db, person_id)
    ]


@router.get("/persons/{person_id}/detail", response_model=PersonDetailResponse)
def get_person_detail(
    person_id: int,
    db: Session = Depends(get_db),  # noqa: B008
) -> PersonDetailResponse:
    """Person card data: aliases, classifications, RF status and evidence-backed events."""
    person = person_read.person(db, person_id)
    if person is None:
        raise HTTPException(status_code=404, detail="Person not found")
    aliases = person_read.aliases(db, person_id, ordered=True)
    return PersonDetailResponse(
        person=_person_response(person),
        aliases=[_alias_response(alias) for alias in aliases],
        persecution=_latest_persecution_response(db, person_id),
        rosfinmonitoring=_latest_rosfin_match_response(db, person_id),
        events=get_person_events(person_id, db),
    )
