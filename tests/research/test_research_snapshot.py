"""One research request reads one PostgreSQL snapshot: a read-only REPEATABLE READ
transaction around the deterministic research, never around the LLM or Qdrant."""

from __future__ import annotations

import ast
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, event, text
from sqlalchemy.orm import Session, sessionmaker
from support.research_db_fixtures import ResearchSeeder
from support.research_workflow_fakes import FakeRequestParser, FakeSnapshotLookup
from support.semantic_fakes import StaticRetriever

from candidates.service import CandidateQueryService
from persecution.models import PersecutionClassificationStatus
from research.models import ResearchRequest
from research.planning.planner import ResearchPlanner
from research.repository import SqlAlchemyPersonResearchRepository
from research.service import ResearchService
from research.unit_of_work import ResearchReaders, SqlAlchemyResearchUnitOfWork
from research.workflow.graph import build_research_graph, run_research_query
from research.workflow.models import ResearchIntake, WorkflowStatus
from semantic_retrieval.models import RetrievalBackend, RetrievalQuery, RetrievalResult
from semantic_retrieval.relevance import DenseSimilarityRelevancePolicy
from sources.source_registry import SOURCES

SRC = Path(__file__).parents[2] / "src"


class RecordingUnitOfWork(SqlAlchemyResearchUnitOfWork):
    """Records when the read transaction opens and closes, into a shared log."""

    def __init__(self, session_factory: sessionmaker[Session], log: list[str]) -> None:
        super().__init__(session_factory)
        self.log = log
        self.open = False

    @contextmanager
    def read(self) -> Iterator[ResearchReaders]:
        self.log.append("transaction opened")
        self.open = True
        try:
            with super().read() as readers:
                yield readers
        finally:
            self.open = False
            self.log.append("transaction closed")


def _political_absent(seed: ResearchSeeder, name: str, snapshot_id: int) -> int:
    person_id = seed.person(name)
    seed.classification(person_id, "political", 0.9, reasons=["антивоенная позиция"])
    seed.match(person_id, snapshot_id, "not_matched", 0.8)
    return person_id


def _request(**criteria: Any) -> ResearchRequest:
    return ResearchRequest.model_validate({"object_type": "person", "criteria": criteria})


@pytest.fixture
def seeded(session_factory: sessionmaker[Session]) -> tuple[int, int]:
    with session_factory() as session:
        seed = ResearchSeeder(session)
        snapshot_id = seed.snapshot()
        person_id = _political_absent(seed, "Иван Петров", snapshot_id)
        session.commit()
    return snapshot_id, person_id


def _counting(
    session_factory: sessionmaker[Session],
) -> tuple[sessionmaker[Session], list[Session]]:
    opened: list[Session] = []

    @event.listens_for(session_factory, "after_begin")
    def record(session: Session, *args: object) -> None:
        opened.append(session)

    return session_factory, opened


def test_every_read_of_one_request_uses_one_session(
    session_factory: sessionmaker[Session], seeded: tuple[int, int]
) -> None:
    """Political AND not_matched runs through CandidateQueryService too."""
    snapshot_id, person_id = seeded
    factory = sessionmaker(bind=session_factory.kw["bind"])
    factory, opened = _counting(factory)
    service = ResearchService(unit_of_work=SqlAlchemyResearchUnitOfWork(factory))

    response = service.execute(
        _request(
            persecution_status="political",
            rosfinmonitoring_status="not_matched",
            snapshot_id=snapshot_id,
        )
    )

    assert [result.person.id for result in response.results] == [person_id]
    assert len(set(opened)) == 1


def test_the_transaction_is_read_only_repeatable_read(
    session_factory: sessionmaker[Session], seeded: tuple[int, int], monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, str] = {}
    original = SqlAlchemyPersonResearchRepository.find_person_ids

    def spying(self: SqlAlchemyPersonResearchRepository, *args: Any, **kwargs: Any) -> list[int]:
        with self._session() as session:
            seen["isolation"] = session.execute(text("SHOW transaction_isolation")).scalar_one()
            seen["read_only"] = session.execute(text("SHOW transaction_read_only")).scalar_one()
        return original(self, *args, **kwargs)

    monkeypatch.setattr(SqlAlchemyPersonResearchRepository, "find_person_ids", spying)

    ResearchService(unit_of_work=SqlAlchemyResearchUnitOfWork(session_factory)).execute(
        _request(name="Петров")
    )

    assert seen == {"isolation": "repeatable read", "read_only": "on"}


def test_a_change_committed_during_the_request_is_not_seen(
    session_factory: sessionmaker[Session],
    seeded: tuple[int, int],
    test_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Filtering sees the person political; a newer non-political classification is
    committed before the details load. The answer keeps the first moment throughout."""
    _, person_id = seeded
    original = SqlAlchemyPersonResearchRepository.find_person_ids

    def then_reclassify(
        self: SqlAlchemyPersonResearchRepository, *args: Any, **kwargs: Any
    ) -> list[int]:
        found = original(self, *args, **kwargs)
        with test_engine.begin() as other:
            other.execute(
                text(
                    "INSERT INTO persecution_classifications (person_id, status, confidence, "
                    "reasons, evidence_types, classifier_name, classifier_version, "
                    "classified_at) VALUES (:person_id, 'non_political', 0.95, '[]', '[]', "
                    "'rule-based', '9.9.9', now() + interval '1 day')"
                ),
                {"person_id": person_id},
            )
        return found

    monkeypatch.setattr(SqlAlchemyPersonResearchRepository, "find_person_ids", then_reclassify)

    response = ResearchService(unit_of_work=SqlAlchemyResearchUnitOfWork(session_factory)).execute(
        _request(persecution_status="political", name="Петров")
    )

    [result] = response.results
    assert result.persecution is not None
    assert result.persecution.status is PersecutionClassificationStatus.POLITICAL


def test_an_exception_rolls_back_and_releases_the_connection(
    session_factory: sessionmaker[Session], seeded: tuple[int, int], monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = session_factory.kw["bind"]

    def broken(self: SqlAlchemyPersonResearchRepository, *args: Any) -> dict[int, Any]:
        raise RuntimeError("details failed")

    monkeypatch.setattr(SqlAlchemyPersonResearchRepository, "get_person_details", broken)
    before = engine.pool.checkedout()

    with pytest.raises(RuntimeError, match="details failed"):
        ResearchService(unit_of_work=SqlAlchemyResearchUnitOfWork(session_factory)).execute(
            _request(name="Петров")
        )

    assert engine.pool.checkedout() == before


def test_the_request_never_commits(
    session_factory: sessionmaker[Session], seeded: tuple[int, int], monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(self: Session) -> None:
        raise AssertionError("research committed")

    monkeypatch.setattr(Session, "commit", refuse)

    response = ResearchService(unit_of_work=SqlAlchemyResearchUnitOfWork(session_factory)).execute(
        _request(name="Петров")
    )

    assert len(response.results) == 1


def test_results_match_readers_with_their_own_sessions(
    session_factory: sessionmaker[Session], seeded: tuple[int, int]
) -> None:
    """On a quiet database the snapshot changes nothing in the answer."""
    snapshot_id, _ = seeded
    request = _request(
        persecution_status="political",
        rosfinmonitoring_status="not_matched",
        snapshot_id=snapshot_id,
    )

    in_snapshot = ResearchService(unit_of_work=SqlAlchemyResearchUnitOfWork(session_factory))
    per_call = ResearchService(
        repository=SqlAlchemyPersonResearchRepository(session_factory),
        candidate_query=CandidateQueryService(session_factory),
    )

    assert in_snapshot.execute(request).model_dump() == per_call.execute(request).model_dump()


class _LoggingParser(FakeRequestParser):
    def __init__(self, intake: ResearchIntake, log: list[str], unit: RecordingUnitOfWork) -> None:
        super().__init__(intake=intake)
        self.log = log
        self.unit = unit

    def parse(self, text: str) -> ResearchIntake:
        self.log.append(f"llm (transaction open: {self.unit.open})")
        return super().parse(text)


class _LoggingRetriever(StaticRetriever):
    def __init__(self, ids: list[int], log: list[str], unit: RecordingUnitOfWork) -> None:
        super().__init__(RetrievalBackend.HYBRID, ids, dense_scores={i: 0.95 for i in ids})
        self.log = log
        self.unit = unit

    def retrieve(self, query: RetrievalQuery) -> RetrievalResult:
        self.log.append(f"qdrant (transaction open: {self.unit.open})")
        return super().retrieve(query)


def test_the_workflow_calls_the_llm_and_qdrant_before_the_read_transaction(
    session_factory: sessionmaker[Session], seeded: tuple[int, int]
) -> None:
    _, person_id = seeded
    log: list[str] = []
    unit = RecordingUnitOfWork(session_factory, log)
    intake = ResearchIntake(
        request={
            "object_type": "person",
            "criteria": {
                "semantic_query": "антивоенная позиция",
                "persecution_status": "political",
            },
        }
    )
    graph = build_research_graph(
        request_parser=_LoggingParser(intake, log, unit),
        research_service=ResearchService(unit_of_work=unit),
        snapshot_lookup=FakeSnapshotLookup(latest=None),
        planner=ResearchPlanner(SOURCES, candidate_pool_size=25),
        candidate_retriever=_LoggingRetriever([person_id], log, unit),
        relevance_policy=DenseSimilarityRelevancePolicy(dense_min_score=0.8),
    )

    result = run_research_query(graph, "Найди людей с антивоенной позицией")

    assert result.status is WorkflowStatus.COMPLETED, result.error
    assert log == [
        "llm (transaction open: False)",
        "qdrant (transaction open: False)",
        "transaction opened",
        "transaction closed",
    ]


def _research_service_calls(path: Path) -> list[ast.Call]:
    return [
        node
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "ResearchService"
    ]


def test_every_production_research_service_reads_one_snapshot() -> None:
    """API, CLI, workflow, MCP and the evaluations all build it on the unit of work."""
    calls: list[tuple[str, list[str]]] = [
        (str(path.relative_to(SRC)), sorted(keyword.arg or "" for keyword in call.keywords))
        for path in sorted(SRC.rglob("*.py"))
        for call in _research_service_calls(path)
    ]

    assert calls, "no ResearchService construction found"
    assert {path for path, _ in calls} >= {
        "web/dependencies.py",
        "cli/research.py",
        "research/workflow_factory.py",
        "platform_api/read_only.py",
    }
    assert all(keywords == ["unit_of_work"] for _, keywords in calls), calls
