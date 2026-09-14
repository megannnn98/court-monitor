"""Deterministic research report: claims, citations, review and report status."""

from __future__ import annotations

from research_report_fixtures import (
    ARTICLE_FULL_TEXT,
    ARTICLE_ID,
    ARTICLE_URL,
    CLASSIFICATION_ID,
    EVENT_ID,
    SNAPSHOT_ID,
    classification,
    event,
    event_evidence,
    mention_evidence,
    person_result,
    request,
    response,
    rosfin,
    source,
    unclassified_result,
)

from candidate_query_models import RosfinmonitoringStatus
from extraction_models import EventType
from persecution_models import PersecutionClassificationStatus
from research_models import (
    PersonResearchResult,
    ResearchEvidenceType,
    ResearchRequest,
    ResearchResponse,
)
from research_planning.models import SourceRoutingReason
from research_planning.planner import ResearchPlanner
from research_reports.builder import ResearchReportBuilder
from research_reports.evaluation import ResearchResultEvaluator
from research_reports.models import (
    ResearchClaim,
    ResearchClaimBasis,
    ResearchClaimType,
    ResearchReport,
    ResearchReportItem,
    ResearchReportStatus,
    ResearchReportWarningCode,
    ResearchReviewReason,
)
from research_reports.review_policy import ResearchReviewPolicy
from source_registry import SOURCES

POLITICAL = PersecutionClassificationStatus.POLITICAL


def _build(
    research_request: ResearchRequest, research_response: ResearchResponse
) -> ResearchReport:
    planner = ResearchPlanner(SOURCES)
    evaluator = ResearchResultEvaluator(planner=planner, review_policy=ResearchReviewPolicy())
    evaluation = evaluator.evaluate(
        request=research_request,
        plan=planner.plan(research_request),
        response=research_response,
    )
    return ResearchReportBuilder().build(
        request=research_request, response=research_response, evaluation=evaluation
    )


def _political_not_matched_request() -> ResearchRequest:
    return request(
        persecution_status=POLITICAL,
        rosfinmonitoring_status=RosfinmonitoringStatus.NOT_MATCHED,
        snapshot_id=SNAPSHOT_ID,
    )


def _single_item(
    result: PersonResearchResult, research_request: ResearchRequest | None = None
) -> ResearchReportItem:
    research_request = research_request or request(snapshot_id=SNAPSHOT_ID)
    report = _build(research_request, response(research_request, [result]))
    (item,) = report.items
    return item


def _claim(item: ResearchReportItem, claim_type: ResearchClaimType) -> ResearchClaim:
    (claim,) = [claim for claim in item.claims if claim.claim_type is claim_type]
    return claim


def _codes(item: ResearchReportItem) -> list[ResearchReviewReason]:
    return [reason.code for reason in item.review.reasons]


# --- A: POLITICAL + NOT_MATCHED --------------------------------------------------------


def test_a_political_not_matched_is_a_complete_cited_report() -> None:
    research_request = _political_not_matched_request()
    result = person_result(rf=rosfin(RosfinmonitoringStatus.NOT_MATCHED))

    report = _build(research_request, response(research_request, [result]))

    assert report.status is ResearchReportStatus.COMPLETE
    assert report.review_required is False
    (item,) = report.items
    assert item.review_required is False
    assert item.persecution_status is POLITICAL
    assert item.persecution_confidence == 0.85
    assert item.persecution_reasons == ["Антивоенная деятельность"]
    assert item.rosfinmonitoring_status is RosfinmonitoringStatus.NOT_MATCHED
    assert item.rosfinmonitoring_confidence == 0.8
    assert item.aliases == ["Ивана Иванова"]
    source_claims = [c for c in item.claims if c.basis is ResearchClaimBasis.SOURCE_DOCUMENTS]
    assert {c.claim_type for c in source_claims} == {
        ResearchClaimType.IDENTITY,
        ResearchClaimType.PERSECUTION_CLASSIFICATION,
        ResearchClaimType.EVENT,
    }
    assert all(claim.citations and claim.supported for claim in source_claims)
    assert item.report_warnings == []


def test_a_why_matched_lists_only_applied_criteria_with_actual_values() -> None:
    item = _single_item(
        person_result(rf=rosfin(RosfinmonitoringStatus.NOT_MATCHED)),
        _political_not_matched_request(),
    )

    assert [(reason.criterion, reason.requested, reason.actual) for reason in item.why_matched] == [
        ("persecution_status", "political (confidence ≥ 0.70)", "political (0.85)"),
        ("rosfinmonitoring_status", "not_matched (snapshot #7)", "not_matched"),
    ]


def test_why_matched_is_empty_for_a_request_without_filters() -> None:
    assert _single_item(person_result(), request()).why_matched == []


def test_why_matched_for_event_and_source_criteria_names_the_matching_data() -> None:
    research_request = request(event_types=[EventType.ARREST], source="ОВД-Инфо")

    item = _single_item(person_result(), research_request)

    assert [(r.criterion, r.requested, r.actual) for r in item.why_matched] == [
        ("events", "arrest", f"arrest 2026-03-05 (event #{EVENT_ID})"),
        ("source", "ОВД-Инфо", "ОВД-Инфо (статей: 1)"),
    ]


def test_rosfinmonitoring_claim_cites_the_snapshot_not_an_article() -> None:
    item = _single_item(person_result(rf=rosfin(RosfinmonitoringStatus.NOT_MATCHED)))

    claim = _claim(item, ResearchClaimType.ROSFINMONITORING_STATUS)
    assert claim.basis is ResearchClaimBasis.ROSFINMONITORING_SNAPSHOT
    assert claim.snapshot_id == SNAPSHOT_ID
    assert claim.citations == []
    assert claim.supported is True
    assert claim.confidence == 0.8


def test_separate_confidences_are_kept_per_claim() -> None:
    item = _single_item(person_result(rf=rosfin(RosfinmonitoringStatus.NOT_MATCHED)))

    assert _claim(item, ResearchClaimType.PERSECUTION_CLASSIFICATION).confidence == 0.85
    assert _claim(item, ResearchClaimType.ROSFINMONITORING_STATUS).confidence == 0.8
    assert _claim(item, ResearchClaimType.EVENT).confidence == event().confidence
    assert _claim(item, ResearchClaimType.IDENTITY).confidence is None


# --- B: POLITICAL + AMBIGUOUS ----------------------------------------------------------


def test_b_political_ambiguous_requires_review() -> None:
    research_request = request(snapshot_id=SNAPSHOT_ID)
    result = person_result(rf=rosfin(RosfinmonitoringStatus.AMBIGUOUS))

    report = _build(research_request, response(research_request, [result]))

    assert report.status is ResearchReportStatus.REVIEW_REQUIRED
    (item,) = report.items
    assert item.review_required is True
    assert _codes(item) == [ResearchReviewReason.ROSFIN_AMBIGUOUS]
    # The fact is copied, never resolved into an absence.
    assert item.rosfinmonitoring_status is RosfinmonitoringStatus.AMBIGUOUS
    claim = _claim(item, ResearchClaimType.ROSFINMONITORING_STATUS)
    assert claim.review_required is True
    assert "не подтвержден" in claim.text


# --- C: UNCERTAIN persecution ----------------------------------------------------------


def test_c_uncertain_persecution_requires_review_and_is_not_called_political() -> None:
    item = _single_item(
        person_result(persecution=classification(PersecutionClassificationStatus.UNCERTAIN))
    )

    assert item.review_required is True
    assert _codes(item) == [ResearchReviewReason.PERSECUTION_UNCERTAIN]
    assert item.persecution_status is PersecutionClassificationStatus.UNCERTAIN
    claim = _claim(item, ResearchClaimType.PERSECUTION_CLASSIFICATION)
    assert claim.review_required is True
    assert claim.classification_id == CLASSIFICATION_ID
    assert "не установлен" in claim.text


# --- D: NO_MATCH_RECORD ----------------------------------------------------------------


def test_d_no_match_record_is_never_phrased_as_absence() -> None:
    research_request = request(snapshot_id=SNAPSHOT_ID)
    result = person_result(rf=rosfin(RosfinmonitoringStatus.NO_MATCH_RECORD))

    report = _build(research_request, response(research_request, [result]))

    (item,) = report.items
    text = item.rosfinmonitoring_summary or ""
    assert "не выполнялось" in text
    assert "отсутствие в перечне не подтверждено" in text
    for forbidden in ("отсутствует в перечне", "нет в перечне", "не найден в перечне"):
        assert forbidden not in text.lower()
    assert item.review_required is False
    assert [w.code for w in item.report_warnings] == [
        ResearchReportWarningCode.ROSFINMONITORING_NOT_MATCHED_YET
    ]
    assert report.status is ResearchReportStatus.PARTIAL


# --- E: INSUFFICIENT_DATA --------------------------------------------------------------


def test_e_insufficient_data_requires_review_and_says_data_is_insufficient() -> None:
    item = _single_item(person_result(rf=rosfin(RosfinmonitoringStatus.INSUFFICIENT_DATA)))

    assert item.review_required is True
    assert _codes(item) == [ResearchReviewReason.ROSFIN_INSUFFICIENT_DATA]
    text = item.rosfinmonitoring_summary or ""
    assert "Недостаточно данных" in text
    assert "отсутствие в перечне не подтверждено" in text
    assert "не найден в перечне" not in text.lower()


def test_not_matched_is_the_only_status_phrased_as_absence() -> None:
    for status in RosfinmonitoringStatus:
        text = _single_item(person_result(rf=rosfin(status))).rosfinmonitoring_summary or ""
        says_absent = "не найден в перечне" in text.lower()
        assert says_absent is (status is RosfinmonitoringStatus.NOT_MATCHED), status


# --- provenance ------------------------------------------------------------------------


def test_citation_points_to_a_source_of_the_same_result_and_keeps_url() -> None:
    result = person_result()
    item = _single_item(result)

    article_ids = {source.article_id for source in result.sources}
    assert item.citations
    for citation in item.citations:
        assert citation.article_id in article_ids
        assert citation.url == ARTICLE_URL
        assert citation.source_name == "ОВД-Инфо"
        assert citation.published_at == source().published_at


def test_citations_are_this_persons_evidence_spans() -> None:
    result = person_result()
    item = _single_item(result)

    assert [(c.evidence_type, c.start_offset, c.end_offset, c.text) for c in item.citations] == [
        (e.evidence_type, e.start_offset, e.end_offset, e.text) for e in result.evidence
    ]
    (mention,) = _claim(item, ResearchClaimType.IDENTITY).citations[:1]
    assert mention.mention_id == mention_evidence().mention_id
    (event_citation,) = _claim(item, ResearchClaimType.EVENT).citations
    assert event_citation.evidence_type is ResearchEvidenceType.EVENT
    assert event_citation.event_id == EVENT_ID


def test_report_never_contains_the_full_article_text() -> None:
    report = _build(request(), response(request(), [person_result()]))

    assert ARTICLE_FULL_TEXT not in report.model_dump_json()


def test_event_without_span_is_not_a_confirmed_source_fact() -> None:
    result = person_result(evidence=[mention_evidence()], events=[event()])

    item = _single_item(result)

    claim = _claim(item, ResearchClaimType.EVENT)
    assert claim.citations == []
    assert claim.supported is False
    assert claim.review_required is True
    assert ResearchReportWarningCode.CLAIM_WITHOUT_CITATION in [
        w.code for w in item.report_warnings
    ]
    assert _codes(item) == [ResearchReviewReason.MISSING_EVIDENCE]


def test_evidence_of_an_unknown_article_is_reported_and_not_cited() -> None:
    result = person_result(evidence=[mention_evidence(), event_evidence(article_id=99)])

    item = _single_item(result)

    assert all(citation.article_id == ARTICLE_ID for citation in item.citations)
    assert ResearchReportWarningCode.EVIDENCE_WITHOUT_SOURCE in [
        w.code for w in item.report_warnings
    ]


def test_domain_warnings_are_copied_unchanged() -> None:
    result = person_result(rf=rosfin(RosfinmonitoringStatus.AMBIGUOUS))

    assert _single_item(result).domain_warnings == result.warnings


def test_unclassified_person_is_partial_not_non_political() -> None:
    research_request = request()
    report = _build(research_request, response(research_request, [unclassified_result()]))

    (item,) = report.items
    assert item.persecution_status is None
    assert [
        c.claim_type
        for c in item.claims
        if c.claim_type is ResearchClaimType.PERSECUTION_CLASSIFICATION
    ] == []
    assert [w.code for w in item.report_warnings] == [
        ResearchReportWarningCode.PERSECUTION_NOT_CLASSIFIED
    ]
    assert report.status is ResearchReportStatus.PARTIAL


# --- report status and empty results ---------------------------------------------------


def test_empty_result_with_refresh_is_insufficient_data_not_absence() -> None:
    research_request = request(persecution_status=POLITICAL)

    report = _build(research_request, response(research_request, []))

    assert report.status is ResearchReportStatus.INSUFFICIENT_DATA
    assert report.source_refresh_recommended is True
    assert set(report.recommended_sources) == set(SOURCES)
    assert "0" in report.summary.text
    assert "рекомендация" in report.summary.text


def test_empty_person_id_lookup_is_no_matches_without_refresh() -> None:
    research_request = request(person_id=999)

    report = _build(research_request, response(research_request, []))

    assert report.status is ResearchReportStatus.NO_MATCHES
    assert report.source_refresh_recommended is False
    assert report.source_routing.reason is SourceRoutingReason.REFRESH_CANNOT_HELP


def test_truncated_results_are_reported() -> None:
    research_request = request(limit=1)

    report = _build(
        research_request, response(research_request, [person_result()], total_matched=3)
    )

    assert report.summary.total_matched == 3
    assert report.summary.returned == 1
    assert ResearchReportWarningCode.RESULTS_TRUNCATED in [w.code for w in report.warnings]


def test_review_outranks_partial_in_report_status() -> None:
    research_request = request(snapshot_id=SNAPSHOT_ID)
    results = [
        person_result(rf=rosfin(RosfinmonitoringStatus.NO_MATCH_RECORD)),
        person_result(person_id=2, rf=rosfin(RosfinmonitoringStatus.AMBIGUOUS)),
    ]

    report = _build(research_request, response(research_request, results))

    assert report.status is ResearchReportStatus.REVIEW_REQUIRED
    assert report.summary.review_required_count == 1
    assert report.summary.partial_count == 1


def test_builder_is_deterministic() -> None:
    research_request = _political_not_matched_request()
    research_response = response(research_request, [person_result(rf=rosfin())])

    assert (
        _build(research_request, research_response).model_dump_json()
        == _build(research_request, research_response).model_dump_json()
    )
