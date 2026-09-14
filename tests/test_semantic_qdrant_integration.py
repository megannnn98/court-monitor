"""Semantic retrieval against a real Qdrant service (opt-in via QDRANT_TEST_URL).

docker compose --profile semantic up -d qdrant
QDRANT_TEST_URL=http://127.0.0.1:6333 uv run pytest -m qdrant
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from qdrant_client import QdrantClient
from research_db_fixtures import ResearchSeeder
from research_workflow_fakes import FakeRequestParser
from semantic_fakes import HashingEmbedder, document
from sqlalchemy.orm import Session, sessionmaker

from candidate_query_service import CandidateQueryService
from research_planning.planner import ResearchPlanner
from research_repository import SqlAlchemyPersonResearchRepository
from research_service import ResearchService
from research_workflow.graph import build_research_graph, run_research_query
from research_workflow.models import ResearchIntake, WorkflowStatus
from rosfinmonitoring_snapshot_lookup import SqlAlchemyRosfinmonitoringSnapshotLookup
from semantic_retrieval.factory import SemanticComponents
from semantic_retrieval.models import RetrievalBackend, RetrievalEntityType
from semantic_retrieval.relevance import DenseSimilarityRelevancePolicy
from semantic_retrieval.vector_store import QdrantVectorStore, VectorPoint
from source_registry import SOURCES

pytestmark = pytest.mark.qdrant
PERSON = RetrievalEntityType.PERSON
MODEL = "fake-hashing-embedder"


@pytest.fixture
def qdrant() -> Iterator[tuple[QdrantClient, dict[RetrievalEntityType, str]]]:
    url = os.environ.get("QDRANT_TEST_URL")
    if not url:
        pytest.skip("QDRANT_TEST_URL is not set")
    client = QdrantClient(url=url, timeout=10, check_compatibility=False)
    suffix = uuid.uuid4().hex[:8]
    collections = {
        PERSON: f"test_persons_{suffix}",
        RetrievalEntityType.EVENT: f"test_events_{suffix}",
    }
    try:
        yield client, collections
    finally:
        for name in collections.values():
            if client.collection_exists(name):
                client.delete_collection(name)


def test_vector_store_round_trip_with_payload_filter(
    qdrant: tuple[QdrantClient, dict[RetrievalEntityType, str]],
) -> None:
    client, collections = qdrant
    store = QdrantVectorStore(client)
    name = collections[PERSON]
    store.ensure_collection(name, 3)

    store.upsert(
        name,
        [
            VectorPoint(document(1, "a"), [1.0, 0.0, 0.0], MODEL),
            VectorPoint(document(2, "b"), [0.8, 0.2, 0.0], MODEL),
            VectorPoint(document(3, "c"), [0.0, 0.0, 1.0], MODEL),
        ],
    )
    store.upsert(name, [VectorPoint(document(1, "a2"), [1.0, 0.0, 0.0], MODEL)])

    assert store.count(name) == 3
    assert [
        m.entity_id for m in store.search(name, [1.0, 0.0, 0.0], embedding_model_id=MODEL, limit=2)
    ] == [1, 2]
    assert [
        m.entity_id
        for m in store.search(
            name, [1.0, 0.0, 0.0], embedding_model_id=MODEL, limit=5, entity_ids=[3]
        )
    ] == [3]
    store.delete(name, PERSON, [2])
    assert store.count(name) == 2


def test_indexed_postgres_persons_are_retrieved_and_researched_through_the_graph(
    session_factory: sessionmaker[Session],
    qdrant: tuple[QdrantClient, dict[RetrievalEntityType, str]],
) -> None:
    client, collections = qdrant
    text = (
        "Суд арестовал Ивана Иванова за пикет против войны. Петра Петрова оштрафовали за парковку."
    )
    with session_factory() as session:
        seed = ResearchSeeder(session)
        source_id = seed.source("ОВД-Инфо", "https://ovd.info")
        _, run_id = seed.article(source_id, external_id="q1", title="Хроника", text=text)
        ivan = seed.person("Иван Иванов")
        petr = seed.person("Пётр Петров")
        seed.event(
            run_id,
            "Суд арестовал Ивана Иванова за пикет против войны",
            event_type="arrest",
            event_date=datetime(2024, 3, 5, tzinfo=UTC),
            links=[(ivan, "subject")],
        )
        seed.event(
            run_id,
            "Петра Петрова оштрафовали за парковку",
            event_type="fine",
            event_date=None,
            links=[(petr, "subject")],
        )
        seed.classification(ivan, "political", 0.9)
        seed.classification(petr, "non_political", 0.9)
        session.commit()

    components = SemanticComponents(
        session_factory=session_factory,
        store=QdrantVectorStore(client),
        embedder=HashingEmbedder(),
        collections=collections,
    )
    stats = components.indexer().rebuild(PERSON)
    assert (stats.documents_built, stats.embedded) == (2, 2)
    assert components.indexer().rebuild(PERSON, incremental=True).unchanged == 2

    graph = build_research_graph(
        request_parser=FakeRequestParser(
            intake=ResearchIntake(
                request={
                    "object_type": "person",
                    "criteria": {
                        "semantic_query": "пикет против войны",
                        "persecution_status": "political",
                    },
                }
            )
        ),
        research_service=ResearchService(
            repository=SqlAlchemyPersonResearchRepository(session_factory),
            candidate_query=CandidateQueryService(session_factory),
        ),
        snapshot_lookup=SqlAlchemyRosfinmonitoringSnapshotLookup(session_factory),
        planner=ResearchPlanner(SOURCES, candidate_pool_size=10),
        candidate_retriever=components.retriever(RetrievalBackend.HYBRID),
        # Hashing vectors are not E5 vectors: a threshold suited to the fake embedder.
        relevance_policy=DenseSimilarityRelevancePolicy(dense_min_score=0.3),
    )

    result = run_research_query(graph, "Люди, преследуемые за пикеты против войны")

    assert result.status is WorkflowStatus.COMPLETED
    assert result.retrieval is not None and ivan in result.retrieval.entity_ids
    # Pётр may be a (weak) candidate, but the POLITICAL filter comes from PostgreSQL.
    assert [r.person.id for r in result.results] == [ivan]
    assert result.report is not None and result.report.items[0].retrieval_rank is not None
