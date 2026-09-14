"""Centralized human review policy for research results."""

from __future__ import annotations

import pytest
from research_report_fixtures import (
    CLASSIFICATION_ID,
    SNAPSHOT_ID,
    classification,
    event,
    event_evidence,
    mention_evidence,
    person_result,
    request,
    rosfin,
    unclassified_result,
)

from candidate_query_models import RosfinmonitoringStatus
from persecution_models import PersecutionClassificationStatus
from research_reports.models import ResearchReviewReason, ResearchReviewSeverity
from research_reports.review_policy import ResearchReviewPolicy

POLICY = ResearchReviewPolicy()


def test_political_not_matched_with_citations_needs_no_review() -> None:
    decision = POLICY.evaluate(
        request=request(), result=person_result(rf=rosfin(RosfinmonitoringStatus.NOT_MATCHED))
    )

    assert decision.required is False
    assert decision.reasons == []
    assert decision.severity is ResearchReviewSeverity.NONE


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (RosfinmonitoringStatus.AMBIGUOUS, ResearchReviewReason.ROSFIN_AMBIGUOUS),
        (RosfinmonitoringStatus.NEEDS_REVIEW, ResearchReviewReason.ROSFIN_NEEDS_REVIEW),
        (RosfinmonitoringStatus.INSUFFICIENT_DATA, ResearchReviewReason.ROSFIN_INSUFFICIENT_DATA),
    ],
)
def test_unresolved_rosfinmonitoring_status_requires_review(
    status: RosfinmonitoringStatus, code: ResearchReviewReason
) -> None:
    decision = POLICY.evaluate(request=request(), result=person_result(rf=rosfin(status)))

    assert decision.required is True
    assert [reason.code for reason in decision.reasons] == [code]
    assert decision.reasons[0].snapshot_id == SNAPSHOT_ID
    assert decision.severity is ResearchReviewSeverity.BLOCKING


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (PersecutionClassificationStatus.UNCERTAIN, ResearchReviewReason.PERSECUTION_UNCERTAIN),
        (
            PersecutionClassificationStatus.NEEDS_REVIEW,
            ResearchReviewReason.PERSECUTION_NEEDS_REVIEW,
        ),
    ],
)
def test_unresolved_persecution_status_requires_review(
    status: PersecutionClassificationStatus, code: ResearchReviewReason
) -> None:
    decision = POLICY.evaluate(
        request=request(), result=person_result(persecution=classification(status))
    )

    assert decision.required is True
    assert [reason.code for reason in decision.reasons] == [code]
    assert decision.reasons[0].classification_id == CLASSIFICATION_ID


def test_no_match_record_is_a_data_gap_not_a_review() -> None:
    decision = POLICY.evaluate(
        request=request(), result=person_result(rf=rosfin(RosfinmonitoringStatus.NO_MATCH_RECORD))
    )

    assert decision.required is False


def test_unclassified_person_is_a_data_gap_not_a_review() -> None:
    assert POLICY.evaluate(request=request(), result=unclassified_result()).required is False


def test_political_below_product_threshold_is_low_confidence() -> None:
    decision = POLICY.evaluate(
        request=request(),
        result=person_result(persecution=classification(confidence=0.5)),
    )

    assert [reason.code for reason in decision.reasons] == [ResearchReviewReason.LOW_CONFIDENCE]
    assert decision.required is True
    assert decision.severity is ResearchReviewSeverity.ADVISORY


def test_classification_without_citable_evidence_is_missing_evidence() -> None:
    decision = POLICY.evaluate(
        request=request(), result=person_result(evidence=[], events=[], sources=[])
    )

    assert [reason.code for reason in decision.reasons] == [ResearchReviewReason.MISSING_EVIDENCE]
    assert decision.severity is ResearchReviewSeverity.BLOCKING


def test_evidence_whose_article_is_not_in_sources_is_not_citable() -> None:
    result = person_result(evidence=[mention_evidence(article_id=99)], events=[])

    decision = POLICY.evaluate(request=request(), result=result)

    assert ResearchReviewReason.MISSING_EVIDENCE in [reason.code for reason in decision.reasons]


def test_event_without_its_own_span_is_missing_evidence() -> None:
    # The person is cited by a mention, but the arrest itself has no event span.
    result = person_result(evidence=[mention_evidence()], events=[event()])

    decision = POLICY.evaluate(request=request(), result=result)

    (reason,) = decision.reasons
    assert reason.code is ResearchReviewReason.MISSING_EVIDENCE
    assert reason.event_id == event().event_id


def test_policy_never_misses_a_review_the_domain_result_already_requires() -> None:
    for persecution_status in PersecutionClassificationStatus:
        for rf_status in RosfinmonitoringStatus:
            result = person_result(
                persecution=classification(persecution_status),
                rf=rosfin(rf_status),
                evidence=[mention_evidence(), event_evidence()],
            )
            decision = POLICY.evaluate(request=request(), result=result)
            if result.review_required:
                assert decision.required, (persecution_status, rf_status)


def test_unclassified_person_without_any_citation_is_missing_evidence() -> None:
    decision = POLICY.evaluate(
        request=request(), result=unclassified_result(evidence=[], events=[], sources=[])
    )

    assert [reason.code for reason in decision.reasons] == [ResearchReviewReason.MISSING_EVIDENCE]
