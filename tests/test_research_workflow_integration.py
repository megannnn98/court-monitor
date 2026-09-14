"""Boundary between part 2 and part 1 over PostgreSQL.

natural language -> fake parser -> LangGraph -> real ResearchService and
snapshot lookup -> test PostgreSQL -> expected Person.
"""

from __future__ import annotations

from datetime import UTC, datetime

from research_db_fixtures import ResearchSeeder
from research_workflow_fakes import FakeRequestParser
from sqlalchemy.orm import Session, sessionmaker

from candidate_query_models import RosfinmonitoringStatus
from candidate_query_service import CandidateQueryService
from research_repository import SqlAlchemyPersonResearchRepository
from research_service import ResearchService
from research_workflow.graph import ResearchGraph, build_research_graph, run_research_query
from research_workflow.models import ResearchIntake, WorkflowErrorCode, WorkflowStatus
from rosfinmonitoring_snapshot_lookup import SqlAlchemyRosfinmonitoringSnapshotLookup

QUERY = (
    "Найди людей, которых преследовали за антивоенную деятельность "
    "и которых нет в перечне Росфинмониторинга"
)
TEXT = "Суд арестовал Виктора Викторова по делу о фейках об армии. Анну Алексееву тоже."


def _graph(session_factory: sessionmaker[Session], parser: FakeRequestParser) -> ResearchGraph:
    return build_research_graph(
        request_parser=parser,
        research_service=ResearchService(
            repository=SqlAlchemyPersonResearchRepository(session_factory),
            candidate_query=CandidateQueryService(session_factory),
        ),
        snapshot_lookup=SqlAlchemyRosfinmonitoringSnapshotLookup(session_factory),
    )


def _parser() -> FakeRequestParser:
    # What a correct intake produces for QUERY: no snapshot, the system picks it.
    return FakeRequestParser(
        intake=ResearchIntake(
            request={
                "object_type": "person",
                "criteria": {
                    "persecution_status": "political",
                    "rosfinmonitoring_status": "not_matched",
                },
            }
        )
    )


def test_natural_language_query_reaches_real_research_service(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        seed = ResearchSeeder(session)
        source_id = seed.source("ОВД-Инфо", "https://ovd.info")
        article_id, run_id = seed.article(source_id, external_id="nl-1", title="Хроника", text=TEXT)
        old_snapshot = seed.snapshot("old")
        seed.entry(old_snapshot, "СТАРЫЙ ЧЕЛОВЕК")
        latest = seed.snapshot("latest")
        seed.entry(latest, "АЛЕКСЕЕВА АННА")
        # Newest snapshot row with no entries = incomplete import, ignored.
        seed.snapshot("broken")

        victor = seed.person("Виктор Викторов")
        anna = seed.person("Анна Алексеева")
        seed.mention(run_id, "Виктора Викторова", person_id=victor)
        seed.event(
            run_id,
            "Суд арестовал Виктора Викторова",
            event_type="arrest",
            event_date=datetime(2024, 3, 5, tzinfo=UTC),
            links=[(victor, "subject")],
        )
        seed.mention(run_id, "Анну Алексееву", person_id=anna)
        for person_id in (victor, anna):
            seed.classification(person_id, "political", 0.9)
        seed.match(victor, latest, "not_matched", 0.9)
        seed.match(anna, latest, "matched", 0.95)
        # In the older snapshot Victor was matched: must not be used.
        seed.match(victor, old_snapshot, "matched", 0.95)
        session.commit()

    # Snapshot dates are equal in fixtures; id breaks the tie, the entry-less
    # "broken" snapshot is skipped.
    result = run_research_query(_graph(session_factory, _parser()), QUERY)

    assert result.status is WorkflowStatus.COMPLETED
    assert result.request is not None
    assert result.request.criteria.snapshot_id == latest
    assert result.warnings == [
        (
            "Snapshot не указан пользователем; использован последний доступный "
            f"snapshot #{latest} от 2024-01-01."
        )
    ]
    assert result.total_matched == 1
    (person,) = result.results
    assert person.person.id == victor
    assert person.rosfinmonitoring is not None
    assert person.rosfinmonitoring.status is RosfinmonitoringStatus.NOT_MATCHED
    assert person.rosfinmonitoring.snapshot_id == latest
    assert [e.event_type.value for e in person.events] == ["arrest"]
    assert [(s.article_id, s.source_name) for s in person.sources] == [(article_id, "ОВД-Инфо")]
    assert {e.text for e in person.evidence} == {
        "Виктора Викторова",
        "Суд арестовал Виктора Викторова",
    }
    assert person.review_required is False


def test_snapshot_lookup_reports_match_count_and_skips_empty_imports(
    session_factory: sessionmaker[Session],
) -> None:
    lookup = SqlAlchemyRosfinmonitoringSnapshotLookup(session_factory)
    assert lookup.latest_imported_snapshot() is None

    with session_factory() as session:
        seed = ResearchSeeder(session)
        imported = seed.snapshot("imported")
        seed.entry(imported, "ИВАНОВ ИВАН")
        seed.entry(imported, "ПЕТРОВ ПЁТР")
        seed.snapshot("empty")
        session.commit()

    summary = lookup.latest_imported_snapshot()

    assert summary is not None
    assert (summary.snapshot_id, summary.entry_count, summary.match_count) == (imported, 2, 0)


def test_no_imported_snapshot_fails_instead_of_returning_nothing(
    session_factory: sessionmaker[Session],
) -> None:
    result = run_research_query(_graph(session_factory, _parser()), QUERY)

    assert result.status is WorkflowStatus.FAILED
    assert result.error is not None
    assert result.error.code is WorkflowErrorCode.NO_ROSFINMONITORING_SNAPSHOT
