"""Deterministic research planning and database-first source routing."""

from __future__ import annotations

from support.research_report_fixtures import SNAPSHOT_ID, person_result, request, response

from candidates.models import RosfinmonitoringStatus
from persecution.models import PersecutionClassificationStatus
from research.planning.models import (
    ResearchDataRequirement,
    ResearchPlanStepType,
    ResearchRetrievalMode,
    SourceDataType,
    SourceRoutingReason,
)
from research.planning.planner import ResearchPlanner, source_capabilities
from sources.source_registry import SOURCES

PLANNER = ResearchPlanner(SOURCES)
REGISTRY_IDS = set(SOURCES)


def test_capabilities_come_from_the_source_registry() -> None:
    capabilities = source_capabilities(SOURCES)

    assert {capability.source_id for capability in capabilities} == REGISTRY_IDS
    for capability in capabilities:
        definition = SOURCES[capability.source_id]
        assert capability.source_name == definition.source_name
        assert capability.base_url == definition.base_url
        assert capability.supports_discovery is definition.supports_discovery
        assert capability.supports_direct_fetch is definition.supports_direct_fetch
        assert capability.data_types == [SourceDataType.ARTICLES]


def test_plan_is_database_first_and_lists_requirements_of_applied_criteria() -> None:
    plan = PLANNER.plan(
        request(
            persecution_status=PersecutionClassificationStatus.POLITICAL,
            rosfinmonitoring_status=RosfinmonitoringStatus.NOT_MATCHED,
            snapshot_id=SNAPSHOT_ID,
        )
    )

    assert plan.database_search is True
    assert [step.step_type for step in plan.steps] == [
        ResearchPlanStepType.DATABASE_SEARCH,
        ResearchPlanStepType.EVALUATE_RESULT,
        ResearchPlanStepType.BUILD_REPORT,
        ResearchPlanStepType.HUMAN_REVIEW_GATE,
    ]
    assert set(plan.data_requirements) == {
        ResearchDataRequirement.PERSONS,
        ResearchDataRequirement.PERSECUTION_CLASSIFICATIONS,
        ResearchDataRequirement.ROSFINMONITORING_MATCHES,
    }
    assert {source.source_id for source in plan.candidate_sources} == REGISTRY_IDS


def test_database_sufficient_when_persons_matched() -> None:
    research_request = request(persecution_status=PersecutionClassificationStatus.POLITICAL)
    plan = PLANNER.plan(research_request)

    decision = PLANNER.route(plan, response(research_request, [person_result()]))

    assert decision.source_refresh_required is False
    assert decision.sources == []
    assert decision.reason is SourceRoutingReason.DATABASE_SUFFICIENT


def test_no_matches_recommends_refreshing_compatible_sources() -> None:
    research_request = request(persecution_status=PersecutionClassificationStatus.POLITICAL)
    plan = PLANNER.plan(research_request)

    decision = PLANNER.route(plan, response(research_request, []))

    assert decision.database_search is True
    assert decision.source_refresh_required is True
    assert set(decision.sources) == REGISTRY_IDS
    assert decision.reason is SourceRoutingReason.NO_MATCHES_IN_DATABASE


def test_explicit_source_filter_limits_recommendation_to_that_source() -> None:
    # The research repository filters by `sources.name`, i.e. the registry source_name.
    research_request = request(source=SOURCES["sota-vision"].source_name)
    plan = PLANNER.plan(research_request)

    decision = PLANNER.route(plan, response(research_request, []))

    assert [source.source_id for source in plan.candidate_sources] == ["sota-vision"]
    assert ResearchDataRequirement.SOURCE_MENTIONS in plan.data_requirements
    assert decision.sources == ["sota-vision"]


def test_registry_id_is_not_a_source_name_and_is_not_routed() -> None:
    # ResearchService filters by exact `sources.name`; "ovd-info" never matches
    # there, so recommending an ovd-info refresh would not change the result.
    plan = PLANNER.plan(request(source="ovd-info"))

    assert plan.candidate_sources == []


def test_unknown_source_filter_never_recommends_other_sources() -> None:
    research_request = request(source="Новая газета")
    plan = PLANNER.plan(research_request)

    decision = PLANNER.route(plan, response(research_request, []))

    assert plan.candidate_sources == []
    assert decision.source_refresh_required is False
    assert decision.sources == []
    assert decision.reason is SourceRoutingReason.NO_COMPATIBLE_SOURCE


def test_empty_lookup_by_person_id_does_not_recommend_refresh() -> None:
    research_request = request(person_id=12345)
    plan = PLANNER.plan(research_request)

    decision = PLANNER.route(plan, response(research_request, []))

    assert decision.source_refresh_required is False
    assert decision.reason is SourceRoutingReason.REFRESH_CANNOT_HELP


def test_event_criteria_require_events() -> None:
    plan = PLANNER.plan(request(event_types=["arrest"]))

    assert ResearchDataRequirement.EVENTS in plan.data_requirements


def test_sources_without_discovery_are_not_recommended() -> None:
    from dataclasses import replace

    planner = ResearchPlanner({"ovd-info": replace(SOURCES["ovd-info"], supports_discovery=False)})
    research_request = request()
    plan = planner.plan(research_request)

    decision = planner.route(plan, response(research_request, []))

    assert decision.source_refresh_required is False
    assert decision.reason is SourceRoutingReason.NO_COMPATIBLE_SOURCE


def test_exact_criteria_are_structured_and_never_need_the_semantic_index() -> None:
    plan = PLANNER.plan(
        request(
            person_id=1,
            name="Иванов",
            persecution_status=PersecutionClassificationStatus.POLITICAL,
            rosfinmonitoring_status=RosfinmonitoringStatus.NOT_MATCHED,
            snapshot_id=SNAPSHOT_ID,
            event_types=["arrest"],
            source="ОВД-Инфо",
        )
    )

    assert plan.retrieval_mode is ResearchRetrievalMode.STRUCTURED
    assert plan.candidate_pool_size == 0
    assert ResearchDataRequirement.SEMANTIC_INDEX not in plan.data_requirements
    assert ResearchPlanStepType.RETRIEVE_CANDIDATES not in [s.step_type for s in plan.steps]


def test_semantic_query_routes_to_hybrid_candidate_retrieval_first() -> None:
    plan = ResearchPlanner(SOURCES, candidate_pool_size=50).plan(
        request(
            semantic_query="антивоенные публикации",
            persecution_status=PersecutionClassificationStatus.POLITICAL,
        )
    )

    assert plan.retrieval_mode is ResearchRetrievalMode.HYBRID
    assert plan.candidate_pool_size == 50
    assert plan.steps[0].step_type is ResearchPlanStepType.RETRIEVE_CANDIDATES
    assert plan.steps[1].step_type is ResearchPlanStepType.DATABASE_SEARCH
    assert ResearchDataRequirement.SEMANTIC_INDEX in plan.data_requirements
    assert ResearchDataRequirement.PERSECUTION_CLASSIFICATIONS in plan.data_requirements


def test_reranking_is_selected_only_by_configuration() -> None:
    semantic = request(semantic_query="пикеты")

    assert (
        ResearchPlanner(SOURCES, rerank_semantic=True).plan(semantic).retrieval_mode
        is ResearchRetrievalMode.HYBRID_RERANKED
    )
    assert (
        ResearchPlanner(SOURCES, rerank_semantic=True).plan(request(name="Иванов")).retrieval_mode
        is ResearchRetrievalMode.STRUCTURED
    )
