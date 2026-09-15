"""Product acceptance (ADR 0014): the main workflows end to end, without manual SQL.

fixture source → monitoring (ingest, extraction, ER v2, classification, RF,
semantic index, findings) → LangGraph research → evidence-backed ResearchReport.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from qdrant_client import QdrantClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from support.monitoring_fixtures import (
    SIDOROV,
    FakeUpstream,
    build_service,
    import_rf_snapshot,
    semantic_indexer,
    table_counts,
)
from support.person_resolution_fixtures import seed_person
from support.research_workflow_fakes import FakeRequestParser
from support.semantic_fakes import HashingEmbedder, UnavailableStore

from candidates.service import CandidateQueryService
from db.orm_models import MonitoringFindingRecord, PersonResolutionDecisionRecord
from monitoring.models import MonitoringRunStatus
from persons.persistence import SqlAlchemyPersonPersistence
from persons.resolution.review import PersonResolutionReviewService, ResolutionReviewAction
from research.models import PersonResearchCriteria as ResearchCriteria
from research.planning.planner import ResearchPlanner
from research.reports.provenance import verify_report_provenance
from research.repository import SqlAlchemyPersonResearchRepository
from research.service import ResearchService
from research.workflow.graph import build_research_graph, run_research_query
from research.workflow.models import (
    ResearchIntake,
    ResearchQueryResult,
    WorkflowErrorCode,
    WorkflowStatus,
)
from rosfinmonitoring.snapshot_lookup import SqlAlchemyRosfinmonitoringSnapshotLookup
from semantic_retrieval.factory import SemanticComponents
from semantic_retrieval.models import RetrievalBackend, RetrievalEntityType, RetrievalQuery
from semantic_retrieval.relevance import DenseSimilarityRelevancePolicy
from semantic_retrieval.vector_store import QdrantVectorStore
from sources.source_registry import SOURCES

POLITICAL_NOT_IN_RF = {"persecution_status": "political", "rosfinmonitoring_status": "not_matched"}
DOMAIN_TABLES = (
    "source_documents",
    "parsed_articles",
    "article_extraction_runs",
    "entity_mentions",
    "extracted_events",
    "persons",
    "person_event_links",
    "persecution_classifications",
    "rosfin_matches",
    "monitoring_findings",
)
COLLECTIONS = {
    RetrievalEntityType.PERSON: "persons_monitoring_test",
    RetrievalEntityType.EVENT: "events_monitoring_test",
}


def _research(
    session_factory: sessionmaker[Session],
    criteria: dict[str, Any],
    *,
    components: SemanticComponents | None = None,
) -> ResearchQueryResult:
    graph = build_research_graph(
        request_parser=FakeRequestParser(
            intake=ResearchIntake(request={"object_type": "person", "criteria": criteria})
        ),
        research_service=ResearchService(
            repository=SqlAlchemyPersonResearchRepository(session_factory),
            candidate_query=CandidateQueryService(session_factory),
        ),
        snapshot_lookup=SqlAlchemyRosfinmonitoringSnapshotLookup(session_factory),
        planner=ResearchPlanner(SOURCES, candidate_pool_size=10),
        candidate_retriever=None
        if components is None
        else components.retriever(RetrievalBackend.HYBRID),
        # Hashing vectors are not E5 vectors: a threshold suited to the fake embedder.
        relevance_policy=DenseSimilarityRelevancePolicy(dense_min_score=0.2),
    )
    return run_research_query(graph, "acceptance query")


def _assert_evidence_backed(
    session_factory: sessionmaker[Session], result: ResearchQueryResult
) -> None:
    assert result.status is WorkflowStatus.COMPLETED, result.error
    assert result.report is not None
    with session_factory() as session:
        assert verify_report_provenance(session, result.report, result.results) == []
    assert all(claim.supported for item in result.report.items for claim in item.claims)


def test_new_publication_becomes_a_finding_and_an_evidence_backed_report(
    session_factory: sessionmaker[Session],
) -> None:
    import_rf_snapshot(session_factory, [("Петр Петров", "03.03.1970")])
    upstream = FakeUpstream()
    upstream.publish("sidorov", SIDOROV)
    service = build_service(
        session_factory,
        {"ovd-info": upstream},
        create_semantic_indexer=lambda: semantic_indexer(session_factory),
    )

    first = service.run_source("ovd-info")
    report_one = _research(session_factory, POLITICAL_NOT_IN_RF)
    counts = table_counts(session_factory, *DOMAIN_TABLES)
    second = service.run_source("ovd-info")
    report_two = _research(session_factory, POLITICAL_NOT_IN_RF)

    assert first.status is MonitoringRunStatus.COMPLETED
    assert first.findings_created == 1
    _assert_evidence_backed(session_factory, report_one)
    [item] = report_one.report.items if report_one.report else []
    assert item.canonical_name == "Сергей Сидоров"
    with session_factory() as session:
        finding = session.scalars(select(MonitoringFindingRecord)).one()
    assert finding.person_id == item.person_id
    assert finding.first_seen_run_id == first.id

    # Repeated run: no duplicates of any kind, same research truth.
    assert second.status is MonitoringRunStatus.COMPLETED
    assert (second.documents_ingested, second.findings_created) == (0, 0)
    assert table_counts(session_factory, *DOMAIN_TABLES) == counts
    assert report_two.report is not None and report_one.report is not None
    assert report_two.report.model_dump(mode="json") == report_one.report.model_dump(mode="json")


def test_review_path_keeps_the_candidate_out_until_a_human_decides(
    session_factory: sessionmaker[Session],
) -> None:
    import_rf_snapshot(session_factory, [("Петр Петров", "03.03.1970")])
    seed_person(session_factory, "Сергей Сидоров")
    seed_person(session_factory, "Сергей Сидоров")
    upstream = FakeUpstream()
    upstream.publish("sidorov", SIDOROV)
    service = build_service(session_factory, {"ovd-info": upstream})

    run = service.run_source("ovd-info")

    assert run.status is MonitoringRunStatus.COMPLETED
    assert run.person_reviews_created == 1
    assert table_counts(session_factory, "monitoring_findings")["monitoring_findings"] == 0
    before = _research(session_factory, POLITICAL_NOT_IN_RF)
    assert before.status is WorkflowStatus.COMPLETED
    assert before.results == []

    with session_factory.begin() as session:
        decision_id = session.scalar(
            select(PersonResolutionDecisionRecord.id).where(
                PersonResolutionDecisionRecord.status == "pending_review"
            )
        )
        assert decision_id is not None
        created = PersonResolutionReviewService(SqlAlchemyPersonPersistence(session_factory)).apply(
            session, decision_id, ResolutionReviewAction.CREATE_NEW_PERSON
        )
    derived = service.run_derived()
    after = _research(session_factory, POLITICAL_NOT_IN_RF)

    assert derived.status is MonitoringRunStatus.COMPLETED
    assert derived.findings_created == 1
    assert upstream.fetches == ["sidorov"]
    _assert_evidence_backed(session_factory, after)
    assert [result.person.id for result in after.results] == [created.person_id]


def test_qdrant_outage_degrades_only_semantic_research_and_catches_up(
    session_factory: sessionmaker[Session],
) -> None:
    import_rf_snapshot(session_factory, [("Петр Петров", "03.03.1970")])
    upstream = FakeUpstream()
    upstream.publish("sidorov", SIDOROV)
    outage = build_service(
        session_factory,
        {"ovd-info": upstream},
        create_semantic_indexer=lambda: semantic_indexer(session_factory, UnavailableStore()),
    )

    run = outage.run_source("ovd-info")
    structured = _research(session_factory, POLITICAL_NOT_IN_RF)
    down = SemanticComponents(
        session_factory=session_factory,
        store=UnavailableStore(),
        embedder=HashingEmbedder(),
        collections=COLLECTIONS,
    )
    semantic_down = _research(
        session_factory, {"semantic_query": "антивоенный митинг"}, components=down
    )

    assert run.status is MonitoringRunStatus.COMPLETED_WITH_ERRORS
    assert run.findings_created == 1
    _assert_evidence_backed(session_factory, structured)
    assert len(structured.results) == 1
    assert semantic_down.status is WorkflowStatus.FAILED
    assert semantic_down.error is not None
    assert semantic_down.error.code is WorkflowErrorCode.SEMANTIC_RETRIEVAL_UNAVAILABLE

    store = QdrantVectorStore(QdrantClient(":memory:"))
    recovered = build_service(
        session_factory,
        {"ovd-info": upstream},
        create_semantic_indexer=lambda: semantic_indexer(session_factory, store),
    )
    catch_up = recovered.run_derived()
    up = SemanticComponents(
        session_factory=session_factory,
        store=store,
        embedder=HashingEmbedder(),
        collections=COLLECTIONS,
    )
    semantic_up = _research(
        session_factory,
        {"semantic_query": "Сергей Сидоров политическое преследование правозащитная деятельность"},
        components=up,
    )

    assert catch_up.semantic_entities_indexed == 2, catch_up
    assert upstream.fetches == ["sidorov"]
    _assert_evidence_backed(session_factory, semantic_up)
    assert [result.person.canonical_name for result in semantic_up.results] == ["Сергей Сидоров"]


def test_interrupted_monitoring_releases_its_source(session_factory: sessionmaker[Session]) -> None:
    upstream = FakeUpstream()
    upstream.publish("sidorov", SIDOROV)
    service = build_service(session_factory, {"ovd-info": upstream})
    original: Callable[..., Any] = service.classify

    def interrupted(handle: Any) -> Any:
        raise KeyboardInterrupt  # SIGINT/SIGTERM surfaces as a BaseException

    service.classify = interrupted  # type: ignore[method-assign]
    with pytest.raises(KeyboardInterrupt):
        service.run_source("ovd-info")
    service.classify = original  # type: ignore[method-assign]

    [aborted] = service.repository.list_runs()
    assert aborted.status is MonitoringRunStatus.FAILED
    assert service.run_source("ovd-info").status is MonitoringRunStatus.COMPLETED


@pytest.fixture
def real_qdrant() -> Iterator[QdrantClient]:
    url = os.environ.get("QDRANT_TEST_URL")
    if not url:
        pytest.skip("QDRANT_TEST_URL is not set")
    client = QdrantClient(url=url, timeout=10, check_compatibility=False)
    yield client
    client.close()


@pytest.mark.qdrant
def test_lost_qdrant_collections_are_rebuilt_from_postgres(
    session_factory: sessionmaker[Session], real_qdrant: QdrantClient
) -> None:
    suffix = uuid.uuid4().hex[:8]
    collections = {
        RetrievalEntityType.PERSON: f"dr_persons_{suffix}",
        RetrievalEntityType.EVENT: f"dr_events_{suffix}",
    }
    upstream = FakeUpstream()
    upstream.publish("sidorov", SIDOROV)
    build_service(session_factory, {"ovd-info": upstream}).run_source("ovd-info")
    components = SemanticComponents(
        session_factory=session_factory,
        store=QdrantVectorStore(real_qdrant),
        embedder=HashingEmbedder(),
        collections=collections,
    )
    query = RetrievalQuery(
        text="правозащитник задержан на антивоенном митинге",
        entity_type=RetrievalEntityType.PERSON,
        limit=5,
    )
    try:
        components.indexer().rebuild(RetrievalEntityType.PERSON)
        components.indexer().rebuild(RetrievalEntityType.EVENT)
        assert components.retriever(RetrievalBackend.DENSE).retrieve(query).hits

        for name in collections.values():
            real_qdrant.delete_collection(name)  # disaster: the derived index is gone

        rebuilt = components.indexer().rebuild(RetrievalEntityType.PERSON)
        components.indexer().rebuild(RetrievalEntityType.EVENT)

        assert rebuilt.embedded == 1
        hits = components.retriever(RetrievalBackend.DENSE).retrieve(query).hits
        assert [hit.entity_id for hit in hits][:1] == [
            SqlAlchemyPersonResearchRepository(session_factory).find_person_ids(
                ResearchCriteria(name="Сидоров")
            )[0]
        ]
    finally:
        for name in collections.values():
            if real_qdrant.collection_exists(name):
                real_qdrant.delete_collection(name)
