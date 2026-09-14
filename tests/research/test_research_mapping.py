"""Pure mapping from stored records to research results (no database)."""

from datetime import UTC, datetime

import pytest

from candidates.models import RosfinmonitoringStatus, resolve_rosfinmonitoring_status
from db.orm_models import (
    EntityMentionRecord,
    ExtractedEventRecord,
    PersecutionClassificationRecord,
    RosfinMatchRecord,
)
from extraction.models import EventEntityRole, EventType
from persecution.models import (
    PersecutionClassification,
    PersecutionClassificationStatus,
    PersecutionEvidenceType,
)
from persons.models import Person
from research.mapping import (
    build_warnings,
    classification_from_record,
    event_evidence,
    mention_evidence,
    research_event,
    rosfinmonitoring_from_match,
)
from research.models import (
    PersonResearchResult,
    ResearchEvidenceType,
    ResearchRosfinmonitoring,
    ResearchWarningCode,
)

NOW = datetime(2024, 3, 1, tzinfo=UTC)


def _classification(status: PersecutionClassificationStatus) -> PersecutionClassification:
    return PersecutionClassification(
        person_id=1,
        status=status,
        confidence=0.9,
        classifier_name="rule-based",
        classifier_version="1.0.0",
    )


def _rosfin(status: RosfinmonitoringStatus) -> ResearchRosfinmonitoring:
    return ResearchRosfinmonitoring(snapshot_id=3, status=status)


@pytest.mark.parametrize(
    ("record_status", "expected"),
    [
        (None, RosfinmonitoringStatus.NO_MATCH_RECORD),
        ("matched", RosfinmonitoringStatus.MATCHED),
        ("not_matched", RosfinmonitoringStatus.NOT_MATCHED),
        ("ambiguous", RosfinmonitoringStatus.AMBIGUOUS),
        ("needs_review", RosfinmonitoringStatus.NEEDS_REVIEW),
        ("insufficient_data", RosfinmonitoringStatus.INSUFFICIENT_DATA),
        ("garbage", RosfinmonitoringStatus.NEEDS_REVIEW),
    ],
)
def test_rosfinmonitoring_status_resolution(
    record_status: str | None, expected: RosfinmonitoringStatus
) -> None:
    assert resolve_rosfinmonitoring_status(record_status) is expected


def test_missing_match_record_maps_to_no_match_record_not_absence() -> None:
    result = rosfinmonitoring_from_match(None, snapshot_id=3)

    assert result == ResearchRosfinmonitoring(
        snapshot_id=3, status=RosfinmonitoringStatus.NO_MATCH_RECORD
    )


def test_match_record_maps_status_confidence_and_candidates() -> None:
    record = RosfinMatchRecord(
        person_id=1,
        snapshot_id=3,
        status="ambiguous",
        confidence=0.6,
        matched_entry_id=None,
        matched_entry_name=None,
        candidate_entries=[
            {
                "entry_id": 7,
                "full_name": "ИВАНОВ ИВАН",
                "normalized_name": "иванов иван",
                "matching_key": "иванов|иван",
                "similarity_score": 0.8,
            }
        ],
        reasons=["two candidates"],
        matched_at=NOW,
    )

    result = rosfinmonitoring_from_match(record, snapshot_id=3)

    assert result.status is RosfinmonitoringStatus.AMBIGUOUS
    assert result.confidence == 0.6
    assert [entry.entry_id for entry in result.candidate_entries] == [7]
    assert result.reasons == ["two candidates"]


def test_classification_record_maps_existing_classification() -> None:
    record = PersecutionClassificationRecord(
        id=5,
        person_id=1,
        status="political",
        confidence=0.85,
        reasons=["political charge"],
        evidence_types=["political_charge"],
        classifier_name="rule-based",
        classifier_version="1.0.0",
        classified_at=NOW,
    )

    result = classification_from_record(record)

    assert result.status is PersecutionClassificationStatus.POLITICAL
    assert result.evidence_types == [PersecutionEvidenceType.POLITICAL_CHARGE]
    assert result.id == 5


def test_mention_evidence_keeps_offsets_and_span_text_only() -> None:
    mention = EntityMentionRecord(
        id=11,
        extraction_run_id=2,
        entity_type="person",
        surface_text="Ивана Иванова",
        start_offset=10,
        end_offset=23,
        person_id=1,
    )

    evidence = mention_evidence(mention, article_id=4)

    assert evidence.evidence_type is ResearchEvidenceType.PERSON_MENTION
    assert (evidence.mention_id, evidence.event_id) == (11, None)
    assert (evidence.article_id, evidence.extraction_run_id) == (4, 2)
    assert (evidence.start_offset, evidence.end_offset) == (10, 23)
    assert evidence.text == "Ивана Иванова"


def _event_record() -> ExtractedEventRecord:
    return ExtractedEventRecord(
        id=21,
        extraction_run_id=2,
        event_type="arrest",
        event_date=NOW,
        start_offset=30,
        end_offset=45,
        confidence=0.7,
        attributes={"charge": "ст. 207.3 УК РФ"},
    )


def test_event_evidence_uses_event_span() -> None:
    evidence = event_evidence(_event_record(), article_id=4, span_text="арестовал суд")

    assert evidence.evidence_type is ResearchEvidenceType.EVENT
    assert (evidence.mention_id, evidence.event_id) == (None, 21)
    assert (evidence.start_offset, evidence.end_offset) == (30, 45)
    assert evidence.text == "арестовал суд"


def test_research_event_keeps_type_date_roles_and_provenance() -> None:
    event = research_event(
        _event_record(),
        roles=[EventEntityRole.TARGET, EventEntityRole.SUBJECT],
        article_id=4,
    )

    assert event.event_type is EventType.ARREST
    assert event.event_date == NOW
    assert event.roles == [EventEntityRole.SUBJECT, EventEntityRole.TARGET]
    assert event.article_id == 4
    assert event.attributes == {"charge": "ст. 207.3 УК РФ"}


def test_confirmed_statuses_produce_no_warnings() -> None:
    warnings = build_warnings(
        _classification(PersecutionClassificationStatus.POLITICAL),
        _rosfin(RosfinmonitoringStatus.NOT_MATCHED),
    )

    assert warnings == []


def test_no_snapshot_requested_produces_no_rosfin_warning() -> None:
    warnings = build_warnings(_classification(PersecutionClassificationStatus.NON_POLITICAL), None)

    assert warnings == []


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (PersecutionClassificationStatus.UNCERTAIN, ResearchWarningCode.PERSECUTION_UNCERTAIN),
        (
            PersecutionClassificationStatus.NEEDS_REVIEW,
            ResearchWarningCode.PERSECUTION_NEEDS_REVIEW,
        ),
    ],
)
def test_unclear_persecution_requires_review(
    status: PersecutionClassificationStatus, code: ResearchWarningCode
) -> None:
    warnings = build_warnings(_classification(status), None)

    assert [(w.code, w.requires_review) for w in warnings] == [(code, True)]


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (RosfinmonitoringStatus.AMBIGUOUS, ResearchWarningCode.ROSFIN_AMBIGUOUS),
        (RosfinmonitoringStatus.NEEDS_REVIEW, ResearchWarningCode.ROSFIN_NEEDS_REVIEW),
        (
            RosfinmonitoringStatus.INSUFFICIENT_DATA,
            ResearchWarningCode.ROSFIN_INSUFFICIENT_DATA,
        ),
    ],
)
def test_unclear_rosfin_status_requires_review(
    status: RosfinmonitoringStatus, code: ResearchWarningCode
) -> None:
    warnings = build_warnings(
        _classification(PersecutionClassificationStatus.POLITICAL), _rosfin(status)
    )

    assert [(w.code, w.requires_review) for w in warnings] == [(code, True)]


def test_missing_pipeline_data_warns_without_requiring_review() -> None:
    warnings = build_warnings(None, _rosfin(RosfinmonitoringStatus.NO_MATCH_RECORD))

    assert [(w.code, w.requires_review) for w in warnings] == [
        (ResearchWarningCode.PERSECUTION_NOT_CLASSIFIED, False),
        (ResearchWarningCode.ROSFIN_NO_MATCH_RECORD, False),
    ]


def test_matched_is_confirmed_and_needs_no_review() -> None:
    warnings = build_warnings(
        _classification(PersecutionClassificationStatus.POLITICAL),
        _rosfin(RosfinmonitoringStatus.MATCHED),
    )

    assert warnings == []


def test_review_required_follows_warnings() -> None:
    person = Person(canonical_name="Иван Иванов", normalized_name="иван иванов", matching_key="k")
    uncertain = PersonResearchResult(
        person=person,
        warnings=build_warnings(_classification(PersecutionClassificationStatus.UNCERTAIN), None),
    )
    unclassified = PersonResearchResult(person=person, warnings=build_warnings(None, None))

    assert uncertain.review_required is True
    assert unclassified.review_required is False
    assert uncertain.model_dump()["review_required"] is True
