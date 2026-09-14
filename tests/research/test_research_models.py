"""Validation rules for structured research requests."""

from datetime import date

import pytest
from pydantic import ValidationError

from candidates.models import DEFAULT_MIN_PERSECUTION_CONFIDENCE, RosfinmonitoringStatus
from extraction.models import EventType
from persecution.models import PersecutionClassificationStatus
from research.models import (
    PersonResearchCriteria,
    ResearchObjectType,
    ResearchRequest,
)


def test_empty_request_is_allowed_with_default_limit() -> None:
    request = ResearchRequest(object_type=ResearchObjectType.PERSON)

    assert request.criteria == PersonResearchCriteria()
    assert request.limit == 20


def test_request_parses_json_body() -> None:
    request = ResearchRequest.model_validate(
        {
            "object_type": "person",
            "criteria": {
                "persecution_status": "political",
                "rosfinmonitoring_status": "not_matched",
                "snapshot_id": 3,
                "event_types": ["arrest"],
                "date_from": "2024-01-01",
            },
            "limit": 5,
        }
    )

    assert request.criteria.persecution_status is PersecutionClassificationStatus.POLITICAL
    assert request.criteria.rosfinmonitoring_status is RosfinmonitoringStatus.NOT_MATCHED
    assert request.criteria.event_types == [EventType.ARREST]
    assert request.criteria.date_from == date(2024, 1, 1)
    assert request.limit == 5


def test_unknown_object_type_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ResearchRequest.model_validate({"object_type": "article"})


@pytest.mark.parametrize("limit", [0, -1, 1001])
def test_limit_out_of_range_is_rejected(limit: int) -> None:
    with pytest.raises(ValidationError):
        ResearchRequest(object_type=ResearchObjectType.PERSON, limit=limit)


def test_unknown_criteria_field_is_rejected() -> None:
    # A typo such as "region" must fail loudly instead of silently not filtering.
    with pytest.raises(ValidationError):
        PersonResearchCriteria.model_validate({"region": "Москва"})


def test_date_from_after_date_to_is_rejected() -> None:
    with pytest.raises(ValidationError, match="date_from must not be after date_to"):
        PersonResearchCriteria(date_from=date(2024, 2, 1), date_to=date(2024, 1, 1))


def test_single_day_date_range_is_allowed() -> None:
    criteria = PersonResearchCriteria(date_from=date(2024, 1, 1), date_to=date(2024, 1, 1))

    assert criteria.date_from == criteria.date_to


def test_rosfinmonitoring_status_requires_snapshot_id() -> None:
    with pytest.raises(ValidationError, match="rosfinmonitoring_status requires snapshot_id"):
        PersonResearchCriteria(rosfinmonitoring_status=RosfinmonitoringStatus.NOT_MATCHED)


def test_snapshot_id_alone_is_allowed() -> None:
    criteria = PersonResearchCriteria(snapshot_id=3)

    assert criteria.rosfinmonitoring_status is None


def test_min_confidence_requires_persecution_status() -> None:
    with pytest.raises(
        ValidationError, match="persecution_min_confidence requires persecution_status"
    ):
        PersonResearchCriteria(persecution_min_confidence=0.5)


@pytest.mark.parametrize("confidence", [-0.1, 1.1])
def test_min_confidence_out_of_range_is_rejected(confidence: float) -> None:
    with pytest.raises(ValidationError):
        PersonResearchCriteria(
            persecution_status=PersecutionClassificationStatus.POLITICAL,
            persecution_min_confidence=confidence,
        )


def test_empty_event_types_is_rejected() -> None:
    with pytest.raises(ValidationError):
        PersonResearchCriteria(event_types=[])


@pytest.mark.parametrize("name", ["", "   "])
def test_blank_name_is_rejected(name: str) -> None:
    with pytest.raises(ValidationError):
        PersonResearchCriteria(name=name)


def test_name_is_stripped() -> None:
    assert PersonResearchCriteria(name="  Иванов ").name == "Иванов"


@pytest.mark.parametrize("field", ["person_id", "snapshot_id"])
def test_non_positive_ids_are_rejected(field: str) -> None:
    with pytest.raises(ValidationError):
        PersonResearchCriteria.model_validate({field: 0})


def test_political_status_defaults_to_candidate_query_threshold() -> None:
    criteria = PersonResearchCriteria(persecution_status=PersecutionClassificationStatus.POLITICAL)

    assert criteria.effective_persecution_min_confidence == DEFAULT_MIN_PERSECUTION_CONFIDENCE


def test_explicit_min_confidence_overrides_default() -> None:
    criteria = PersonResearchCriteria(
        persecution_status=PersecutionClassificationStatus.POLITICAL,
        persecution_min_confidence=0.3,
    )

    assert criteria.effective_persecution_min_confidence == 0.3


@pytest.mark.parametrize(
    "status",
    [
        PersecutionClassificationStatus.NON_POLITICAL,
        PersecutionClassificationStatus.UNCERTAIN,
        PersecutionClassificationStatus.NEEDS_REVIEW,
    ],
)
def test_non_political_statuses_have_no_default_threshold(
    status: PersecutionClassificationStatus,
) -> None:
    criteria = PersonResearchCriteria(persecution_status=status)

    assert criteria.effective_persecution_min_confidence is None
