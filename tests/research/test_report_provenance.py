"""Report factuality: claim → citation → evidence → article span → source URL (PostgreSQL)."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from sqlalchemy.orm import Session, sessionmaker
from support.monitoring_fixtures import SIDOROV, FakeUpstream, build_service, import_rf_snapshot
from support.research_workflow_fakes import FakeRequestParser

from candidates.service import CandidateQueryService
from research.planning.planner import ResearchPlanner
from research.reports.models import ResearchClaimType, ResearchReport
from research.reports.provenance import verify_report_provenance
from research.repository import SqlAlchemyPersonResearchRepository
from research.service import ResearchService
from research.workflow.graph import build_research_graph, run_research_query
from research.workflow.models import ResearchIntake, ResearchQueryResult, WorkflowStatus
from rosfinmonitoring.snapshot_lookup import SqlAlchemyRosfinmonitoringSnapshotLookup
from sources.source_registry import SOURCES

QUERY = "Найди политически преследуемых, которых нет в Росфинмониторинге"


def _query(session_factory: sessionmaker[Session]) -> ResearchQueryResult:
    import_rf_snapshot(session_factory, [("Петр Петров", "03.03.1970")])
    upstream = FakeUpstream()
    upstream.publish("sidorov", SIDOROV)
    build_service(session_factory, {"ovd-info": upstream}).run_source("ovd-info")
    graph = build_research_graph(
        request_parser=FakeRequestParser(
            intake=ResearchIntake(
                request={
                    "object_type": "person",
                    "criteria": {
                        "persecution_status": "political",
                        "rosfinmonitoring_status": "not_matched",
                    },
                }
            )
        ),
        research_service=ResearchService(
            repository=SqlAlchemyPersonResearchRepository(session_factory),
            candidate_query=CandidateQueryService(session_factory),
        ),
        snapshot_lookup=SqlAlchemyRosfinmonitoringSnapshotLookup(session_factory),
        planner=ResearchPlanner(SOURCES),
    )
    result = run_research_query(graph, QUERY)
    assert result.status is WorkflowStatus.COMPLETED
    assert result.report is not None
    return result


def test_every_claim_of_a_real_report_traces_to_a_source_document(
    session_factory: sessionmaker[Session],
) -> None:
    result = _query(session_factory)
    assert result.report is not None
    claim_types = {claim.claim_type for item in result.report.items for claim in item.claims}

    with session_factory() as session:
        problems = verify_report_provenance(session, result.report, result.results)

    assert problems == []
    assert {
        ResearchClaimType.IDENTITY,
        ResearchClaimType.PERSECUTION_CLASSIFICATION,
        ResearchClaimType.ROSFINMONITORING_STATUS,
    } <= claim_types
    citations = [
        c for item in result.report.items for claim in item.claims for c in claim.citations
    ]
    assert citations
    assert all(c.url == "https://ovd-info.test/sidorov" for c in citations)


def _tamper_citation_text(report: ResearchReport) -> None:
    report.items[0].claims[0].citations[0].text = "Иван Иванов призывал к насилию"


def _tamper_citation_url(report: ResearchReport) -> None:
    report.items[0].claims[0].citations[0].url = "https://invented.example/article"


def _drop_citations(report: ResearchReport) -> None:
    for claim in report.items[0].claims:
        claim.citations = []


def _invent_classification(report: ResearchReport) -> None:
    for claim in report.items[0].claims:
        if claim.claim_type is ResearchClaimType.PERSECUTION_CLASSIFICATION:
            claim.classification_id = 999_999


def _invent_person(report: ResearchReport) -> None:
    report.items.append(report.items[0].model_copy(update={"person_id": 999_999}))


def _invent_status(report: ResearchReport) -> None:
    from candidates.models import RosfinmonitoringStatus

    report.items[0].rosfinmonitoring_status = RosfinmonitoringStatus.MATCHED


@pytest.mark.parametrize(
    ("tamper", "problem"),
    [
        (_tamper_citation_text, "citation text is not the span"),
        (_tamper_citation_url, "citation URL is not the source's"),
        (_drop_citations, "source claim without citation"),
        (_invent_classification, "classification is not the result's"),
        (_invent_person, "person is not in the research result"),
        (_invent_status, "Rosfinmonitoring status differs"),
    ],
)
def test_facts_not_in_the_domain_result_are_detected(
    session_factory: sessionmaker[Session],
    tamper: Callable[[ResearchReport], None],
    problem: str,
) -> None:
    result = _query(session_factory)
    assert result.report is not None
    report = result.report.model_copy(deep=True)

    tamper(report)

    with session_factory() as session:
        problems = verify_report_provenance(session, report, result.results)
    assert problem in [item.problem for item in problems]
