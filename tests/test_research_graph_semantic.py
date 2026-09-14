"""Compiled graph: semantic candidate retrieval before deterministic research."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from research_report_fixtures import classification, person_result, request, response
from research_workflow_fakes import FakeRequestParser, FakeResearchService, FakeSnapshotLookup
from semantic_fakes import StaticRetriever

from persecution_models import PersecutionClassificationStatus
from research_planning.models import ResearchRetrievalMode
from research_planning.planner import ResearchPlanner
from research_reports.models import (
    ResearchClaimType,
    ResearchReportStatus,
    ResearchReportWarningCode,
    ResearchReviewReason,
)
from research_workflow.graph import ResearchGraph, build_research_graph, run_research_query
from research_workflow.models import (
    ResearchIntake,
    RosfinmonitoringSnapshotSummary,
    UnsupportedCriterion,
    WorkflowErrorCode,
    WorkflowStatus,
)
from semantic_retrieval.models import (
    RetrievalBackend,
    RetrievalUnavailableError,
    VectorSizeMismatchError,
)
from semantic_retrieval.relevance import DenseSimilarityRelevancePolicy
from source_registry import SOURCES

LATEST = RosfinmonitoringSnapshotSummary(
    snapshot_id=7, snapshot_date=datetime(2026, 9, 1, tzinfo=UTC), entry_count=1, match_count=1
)
QUERY = "Найди людей, которых преследовали за антивоенные публикации"


def _parser(criteria: dict[str, Any], **extra: Any) -> FakeRequestParser:
    return FakeRequestParser(
        intake=ResearchIntake(request={"object_type": "person", "criteria": criteria}, **extra)
    )


def _graph(
    parser: FakeRequestParser,
    service: FakeResearchService,
    retriever: StaticRetriever | None,
) -> ResearchGraph:
    return build_research_graph(
        request_parser=parser,
        research_service=service,
        snapshot_lookup=FakeSnapshotLookup(latest=LATEST),
        planner=ResearchPlanner(SOURCES, candidate_pool_size=25),
        candidate_retriever=retriever,
        relevance_policy=DenseSimilarityRelevancePolicy(dense_min_score=0.8),
    )


def _visited(graph: ResearchGraph, query: str) -> list[str]:
    nodes: list[str] = []
    for update in graph.stream({"raw_query": query}, stream_mode="updates"):
        nodes.extend(update)
    return nodes


def test_structured_request_never_touches_the_retriever() -> None:
    retriever = StaticRetriever(RetrievalBackend.HYBRID, error=AssertionError("must not be called"))
    service = FakeResearchService()
    graph = _graph(_parser({"name": "Иванов"}), service, retriever)

    assert "retrieve_candidates" not in _visited(graph, "Найди Иванова")
    result = run_research_query(graph, "Найди Иванова")

    assert result.status is WorkflowStatus.COMPLETED
    assert retriever.queries == []
    assert service.candidate_calls[-1] is None
    assert (
        result.plan is not None and result.plan.retrieval_mode is ResearchRetrievalMode.STRUCTURED
    )
    assert result.retrieval is None


def test_structured_request_works_without_any_semantic_configuration() -> None:
    result = run_research_query(
        _graph(_parser({"persecution_status": "political"}), FakeResearchService(), None), "x"
    )

    assert result.status is WorkflowStatus.COMPLETED


def test_semantic_request_retrieves_candidates_then_runs_research_on_them() -> None:
    research_request = request(
        semantic_query="антивоенные публикации", persecution_status="political"
    )
    service = FakeResearchService(
        response=response(
            research_request, [person_result(person_id=12), person_result(person_id=5)]
        )
    )
    retriever = StaticRetriever(
        RetrievalBackend.HYBRID, [12, 5, 40], dense_scores={i: 0.9 for i in [12, 5, 40]}
    )
    graph = _graph(
        _parser({"semantic_query": "антивоенные публикации", "persecution_status": "political"}),
        service,
        retriever,
    )

    visited = _visited(graph, QUERY)
    result = run_research_query(graph, QUERY)

    assert visited.index("build_research_plan") < visited.index("retrieve_candidates")
    assert visited.index("retrieve_candidates") < visited.index("research")
    assert len(retriever.queries) == 2  # once for the stream above, once for the run
    query = retriever.queries[-1]
    assert (query.text, query.entity_type.value, query.limit) == (
        "антивоенные публикации",
        "person",
        25,
    )
    assert service.candidate_calls[-1] == [12, 5, 40]
    assert result.status is WorkflowStatus.COMPLETED
    assert result.retrieval is not None and result.retrieval.entity_ids == [12, 5, 40]

    report = result.report
    assert report is not None
    assert report.retrieval.mode is ResearchRetrievalMode.HYBRID
    assert (report.retrieval.candidate_pool_size, report.retrieval.candidates_returned) == (25, 3)
    assert [item.retrieval_rank for item in report.items] == [1, 2]
    assert ResearchReportWarningCode.SEMANTIC_CANDIDATE_POOL in [w.code for w in report.warnings]
    semantic_reason = report.items[0].why_matched[-1]
    assert semantic_reason.criterion == "semantic_query"
    assert "не установленный факт" in semantic_reason.actual
    assert "Среди 3 семантически релевантных кандидатов" in report.summary.text
    assert (report.retrieval.candidates_accepted, report.retrieval.min_similarity) == (3, 0.8)
    # The report never exposes retrieval scores.
    assert "score" not in report.model_dump_json()


def test_retrieval_score_is_not_a_domain_confidence_and_does_not_waive_review() -> None:
    research_request = request(semantic_query="пикеты")
    uncertain = person_result(
        person_id=3,
        persecution=classification(
            PersecutionClassificationStatus.UNCERTAIN, confidence=0.4, person_id=3
        ),
    )
    service = FakeResearchService(response=response(research_request, [uncertain]))
    graph = _graph(
        _parser({"semantic_query": "пикеты"}),
        service,
        StaticRetriever(RetrievalBackend.HYBRID, [3], dense_scores={i: 0.9 for i in [3]}),
    )

    result = run_research_query(graph, "пикеты")

    assert result.retrieval is not None and result.retrieval.hits[0].score == 1.0
    assert result.report is not None
    (item,) = result.report.items
    assert item.persecution_status is PersecutionClassificationStatus.UNCERTAIN
    (claim,) = [
        c for c in item.claims if c.claim_type is ResearchClaimType.PERSECUTION_CLASSIFICATION
    ]
    assert claim.confidence == 0.4
    assert [reason.code for reason in item.review.reasons] == [
        ResearchReviewReason.PERSECUTION_UNCERTAIN
    ]
    assert result.report.status is ResearchReportStatus.REVIEW_REQUIRED


def test_unavailable_vector_store_is_a_failure_not_an_empty_result() -> None:
    service = FakeResearchService()
    graph = _graph(
        _parser({"semantic_query": "пикеты"}),
        service,
        StaticRetriever(RetrievalBackend.HYBRID, error=RetrievalUnavailableError("Qdrant down")),
    )

    result = run_research_query(graph, "пикеты")

    assert result.status is WorkflowStatus.FAILED
    assert result.error is not None
    assert result.error.code is WorkflowErrorCode.SEMANTIC_RETRIEVAL_UNAVAILABLE
    assert "не пустой результат" in result.error.message
    assert service.requests == []
    assert (result.results, result.report) == ([], None)


def test_embedding_failures_are_retrieval_unavailable() -> None:
    graph = _graph(
        _parser({"semantic_query": "пикеты"}),
        FakeResearchService(),
        StaticRetriever(RetrievalBackend.HYBRID, error=VectorSizeMismatchError("384 != 768")),
    )

    result = run_research_query(graph, "пикеты")

    assert result.error is not None
    assert result.error.code is WorkflowErrorCode.SEMANTIC_RETRIEVAL_UNAVAILABLE


def test_semantic_request_without_configured_retriever_fails_explicitly() -> None:
    service = FakeResearchService()

    result = run_research_query(
        _graph(_parser({"semantic_query": "пикеты"}), service, None), "пикеты"
    )

    assert result.error is not None
    assert result.error.code is WorkflowErrorCode.SEMANTIC_RETRIEVAL_NOT_CONFIGURED
    assert service.requests == []


def test_unsupported_criteria_are_not_replaced_by_semantic_search() -> None:
    retriever = StaticRetriever(RetrievalBackend.HYBRID, [1], dense_scores={i: 0.9 for i in [1]})
    service = FakeResearchService()
    parser = _parser(
        {"semantic_query": "политически преследуемые"},
        unsupported_criteria=[
            UnsupportedCriterion(criterion="occupation", value="программисты"),
            UnsupportedCriterion(criterion="age", value="30–35 лет"),
        ],
    )

    result = run_research_query(_graph(parser, service, retriever), "программисты 30–35 лет")

    assert result.status is WorkflowStatus.CLARIFICATION_REQUIRED
    assert retriever.queries == []
    assert service.requests == []


def test_empty_candidate_pool_completes_with_explicit_scope() -> None:
    service = FakeResearchService()

    result = run_research_query(
        _graph(
            _parser({"semantic_query": "пикеты"}),
            service,
            StaticRetriever(RetrievalBackend.HYBRID, []),
        ),
        "пикеты",
    )

    assert result.status is WorkflowStatus.COMPLETED
    assert service.candidate_calls[-1] == []
    assert result.report is not None
    assert result.report.summary.text.startswith(
        "В текущем индексе не найдено сущностей с достаточной семантической релевантностью"
    )


# --- relevance acceptance ------------------------------------------------------------


def test_all_rejected_candidates_complete_with_zero_matches_not_everyone() -> None:
    # G: nearest neighbours exist but none is similar enough.
    research_request = request(semantic_query="выращивание бананов на Марсе")
    everyone = response(research_request, [person_result(person_id=1), person_result(person_id=2)])
    service = FakeResearchService(response=everyone)
    retriever = StaticRetriever(
        RetrievalBackend.HYBRID, [1, 2, 3], dense_scores={1: 0.74, 2: 0.73, 3: 0.72}
    )

    result = run_research_query(
        _graph(_parser({"semantic_query": "выращивание бананов на Марсе"}), service, retriever),
        "Найди людей, которых преследовали за выращивание бананов на Марсе",
    )

    assert result.status is WorkflowStatus.COMPLETED
    assert result.error is None
    # [] reaches ResearchService: "no candidates", never "no restriction" (None).
    assert service.candidate_calls[-1] == []
    assert result.semantic_acceptance is not None
    assert result.semantic_acceptance.rejected_count == 3
    assert result.semantic_acceptance.accepted.hits == []
    assert result.retrieval is not None and result.retrieval.entity_ids == [1, 2, 3]


def test_only_accepted_candidates_are_researched_in_retrieval_order() -> None:
    # D: one strong among weak neighbours.
    research_request = request(semantic_query="антивоенная позиция")
    service = FakeResearchService(response=response(research_request, [person_result(person_id=8)]))
    retriever = StaticRetriever(
        RetrievalBackend.HYBRID, [5, 8, 9], dense_scores={5: 0.78, 8: 0.84, 9: 0.79}
    )

    result = run_research_query(
        _graph(_parser({"semantic_query": "антивоенная позиция"}), service, retriever), "x"
    )

    assert service.candidate_calls[-1] == [8]
    assert result.report is not None
    (item,) = result.report.items
    assert item.retrieval_rank == 1
    assert (
        result.report.retrieval.candidates_returned,
        result.report.retrieval.candidates_accepted,
    ) == (3, 1)


def test_graph_visits_accept_candidates_between_retrieval_and_research() -> None:
    graph = _graph(
        _parser({"semantic_query": "пикеты"}),
        FakeResearchService(),
        StaticRetriever(RetrievalBackend.HYBRID, [1], dense_scores={1: 0.9}),
    )

    visited = _visited(graph, "пикеты")

    assert (
        visited.index("retrieve_candidates")
        < visited.index("accept_candidates")
        < visited.index("research")
    )


def test_semantic_retriever_without_relevance_policy_is_not_configured() -> None:
    service = FakeResearchService()
    graph = build_research_graph(
        request_parser=_parser({"semantic_query": "пикеты"}),
        research_service=service,
        snapshot_lookup=FakeSnapshotLookup(latest=LATEST),
        planner=ResearchPlanner(SOURCES),
        candidate_retriever=StaticRetriever(RetrievalBackend.HYBRID, [1], dense_scores={1: 0.9}),
    )

    result = run_research_query(graph, "пикеты")

    assert result.error is not None
    assert result.error.code is WorkflowErrorCode.SEMANTIC_RETRIEVAL_NOT_CONFIGURED
    assert service.requests == []
