"""In-memory builders for research report, review policy and planner tests."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from candidates.models import RosfinmonitoringStatus
from extraction.models import EventEntityRole, EventType
from persecution.models import PersecutionClassification, PersecutionClassificationStatus
from persons.models import AliasOrigin, Person, PersonAlias
from research.mapping import build_warnings
from research.models import (
    PersonResearchCriteria,
    PersonResearchResult,
    ResearchEvent,
    ResearchEvidence,
    ResearchEvidenceType,
    ResearchObjectType,
    ResearchRequest,
    ResearchResponse,
    ResearchRosfinmonitoring,
    ResearchSource,
)

PERSON_ID = 1
ARTICLE_ID = 4
EVENT_ID = 21
CLASSIFICATION_ID = 31
SNAPSHOT_ID = 7
ARTICLE_URL = "https://ovd.info/articles/1"
# Longer than any span: a report must never contain it.
ARTICLE_FULL_TEXT = (
    "Суд в Москве арестовал Ивана Иванова за антивоенный пикет. "
    "Это полный текст статьи, который не должен попадать в отчёт целиком."
)


def request(limit: int = 20, **criteria: Any) -> ResearchRequest:
    return ResearchRequest(
        object_type=ResearchObjectType.PERSON,
        criteria=PersonResearchCriteria(**criteria),
        limit=limit,
    )


def classification(
    status: PersecutionClassificationStatus = PersecutionClassificationStatus.POLITICAL,
    *,
    confidence: float = 0.85,
    person_id: int = PERSON_ID,
) -> PersecutionClassification:
    return PersecutionClassification(
        id=CLASSIFICATION_ID,
        person_id=person_id,
        status=status,
        confidence=confidence,
        reasons=["Антивоенная деятельность"],
        classifier_name="rule-based-persecution-classifier",
        classifier_version="1.0.0",
        classified_at=datetime(2026, 9, 1, tzinfo=UTC),
    )


def rosfin(
    status: RosfinmonitoringStatus = RosfinmonitoringStatus.NOT_MATCHED,
    *,
    confidence: float | None = 0.8,
) -> ResearchRosfinmonitoring:
    if status is RosfinmonitoringStatus.NO_MATCH_RECORD:
        return ResearchRosfinmonitoring(snapshot_id=SNAPSHOT_ID, status=status)
    return ResearchRosfinmonitoring(snapshot_id=SNAPSHOT_ID, status=status, confidence=confidence)


def mention_evidence(article_id: int = ARTICLE_ID) -> ResearchEvidence:
    return ResearchEvidence(
        evidence_type=ResearchEvidenceType.PERSON_MENTION,
        article_id=article_id,
        extraction_run_id=2,
        start_offset=23,
        end_offset=35,
        text="Ивана Иванова",
        mention_id=11,
    )


def event_evidence(article_id: int = ARTICLE_ID, event_id: int = EVENT_ID) -> ResearchEvidence:
    return ResearchEvidence(
        evidence_type=ResearchEvidenceType.EVENT,
        article_id=article_id,
        extraction_run_id=2,
        start_offset=0,
        end_offset=57,
        text="Суд в Москве арестовал Ивана Иванова за антивоенный пикет",
        event_id=event_id,
    )


def event(article_id: int = ARTICLE_ID, event_id: int = EVENT_ID) -> ResearchEvent:
    return ResearchEvent(
        event_id=event_id,
        event_type=EventType.ARREST,
        event_date=datetime(2026, 3, 5, tzinfo=UTC),
        roles=[EventEntityRole.SUBJECT],
        confidence=0.9,
        article_id=article_id,
    )


def source(article_id: int = ARTICLE_ID) -> ResearchSource:
    return ResearchSource(
        article_id=article_id,
        article_title="Арест за пикет",
        source_name="ОВД-Инфо",
        url=ARTICLE_URL,
        published_at=datetime(2026, 3, 6, tzinfo=UTC),
    )


def person_result(
    *,
    persecution: PersecutionClassification | None = None,
    rf: ResearchRosfinmonitoring | None = None,
    evidence: list[ResearchEvidence] | None = None,
    events: list[ResearchEvent] | None = None,
    sources: list[ResearchSource] | None = None,
    person_id: int = PERSON_ID,
) -> PersonResearchResult:
    """Defaults: a POLITICAL person with one cited mention and one cited arrest."""
    persecution = classification() if persecution is None else persecution
    return PersonResearchResult(
        person=Person(
            id=person_id,
            canonical_name="Иван Иванов",
            normalized_name="иван иванов",
            matching_key=f"key-{person_id}",
        ),
        aliases=[
            PersonAlias(
                person_id=person_id,
                surface_text="Ивана Иванова",
                normalized_text="ивана иванова",
                matching_key=f"key-{person_id}",
                origin=AliasOrigin.EXTRACTION,
                confidence=1.0,
                source_mention_id=11,
            )
        ],
        persecution=persecution,
        rosfinmonitoring=rf,
        events=[event()] if events is None else events,
        evidence=[mention_evidence(), event_evidence()] if evidence is None else evidence,
        sources=[source()] if sources is None else sources,
        warnings=build_warnings(persecution, rf),
    )


def unclassified_result(**kwargs: Any) -> PersonResearchResult:
    result = person_result(**kwargs)
    return result.model_copy(
        update={"persecution": None, "warnings": build_warnings(None, result.rosfinmonitoring)}
    )


def response(
    research_request: ResearchRequest,
    results: list[PersonResearchResult],
    *,
    total_matched: int | None = None,
) -> ResearchResponse:
    return ResearchResponse(
        object_type=research_request.object_type,
        request=research_request,
        results=results,
        total_matched=len(results) if total_matched is None else total_matched,
    )
