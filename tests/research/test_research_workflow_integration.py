"""Boundary between part 2 and part 1 over PostgreSQL.

natural language -> fake parser -> LangGraph -> real ResearchService and
snapshot lookup -> test PostgreSQL -> expected Person.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session, sessionmaker
from support.research_db_fixtures import ResearchSeeder
from support.research_workflow_fakes import FakeRequestParser

from candidates.models import RosfinmonitoringStatus
from candidates.service import CandidateQueryService
from research.models import ResearchRequest
from research.planning.planner import ResearchPlanner
from research.reports.models import ResearchReportStatus, ResearchReviewReason
from research.repository import SqlAlchemyPersonResearchRepository
from research.service import ResearchService
from research.workflow.graph import ResearchGraph, build_research_graph, run_research_query
from research.workflow.models import ResearchIntake, WorkflowErrorCode, WorkflowStatus
from rosfinmonitoring.snapshot_lookup import SqlAlchemyRosfinmonitoringSnapshotLookup
from sources.source_registry import SOURCES

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
        planner=ResearchPlanner(SOURCES),
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


def test_graph_and_direct_research_service_agree_on_every_status(
    session_factory: sessionmaker[Session],
) -> None:
    """Same ResearchRequest through ResearchService and through LangGraph.

    The graph must not change latest classification, any Rosfinmonitoring
    status (including NO_MATCH_RECORD) or review_required.
    """
    with session_factory() as session:
        seed = ResearchSeeder(session)
        source_id = seed.source("ОВД-Инфо", "https://ovd.info")
        text = "Упомянуты Альфа, Бета, Гамма, Дельта, Эпсилон, Дзета, Эта."
        _, run_id = seed.article(source_id, external_id="all-statuses", title="Все", text=text)
        snapshot_id = seed.snapshot("statuses")
        seed.entry(snapshot_id, "ЛЮБАЯ ЗАПИСЬ")

        people: dict[str, int] = {}
        for name, rf_status in [
            ("Альфа", "matched"),
            ("Бета", "not_matched"),
            ("Гамма", "ambiguous"),
            ("Дельта", "needs_review"),
            ("Эпсилон", "insufficient_data"),
            ("Дзета", None),  # never matched: NO_MATCH_RECORD
        ]:
            person_id = seed.person(name)
            people[name] = person_id
            seed.mention(run_id, name, person_id=person_id)
            seed.classification(person_id, "political", 0.9)
            if rf_status is not None:
                seed.match(person_id, snapshot_id, rf_status, 0.7)
        # Older POLITICAL superseded by a newer UNCERTAIN classification.
        eta = seed.person("Эта")
        people["Эта"] = eta
        seed.mention(run_id, "Эта", person_id=eta)
        seed.classification(
            eta,
            "political",
            0.95,
            classifier_version="1.0.0",
            classified_at=datetime(2024, 1, 1, tzinfo=UTC),
        )
        seed.classification(
            eta,
            "uncertain",
            0.5,
            classifier_version="2.0.0",
            classified_at=datetime(2024, 6, 1, tzinfo=UTC),
        )
        seed.match(eta, snapshot_id, "not_matched", 0.8)
        session.commit()

    service = ResearchService(
        repository=SqlAlchemyPersonResearchRepository(session_factory),
        candidate_query=CandidateQueryService(session_factory),
    )
    criteria = {"snapshot_id": snapshot_id}
    direct = service.execute(
        ResearchRequest.model_validate({"object_type": "person", "criteria": criteria})
    )
    graph = build_research_graph(
        request_parser=FakeRequestParser(
            intake=ResearchIntake(request={"object_type": "person", "criteria": criteria})
        ),
        research_service=service,
        snapshot_lookup=SqlAlchemyRosfinmonitoringSnapshotLookup(session_factory),
        planner=ResearchPlanner(SOURCES),
    )

    via_graph = run_research_query(graph, f"Покажи всех по snapshot #{snapshot_id}")

    assert via_graph.status is WorkflowStatus.COMPLETED
    assert via_graph.request == direct.request
    assert via_graph.results == direct.results
    assert via_graph.total_matched == direct.total_matched
    by_name = {
        result.person.canonical_name: (
            result.persecution.status.value if result.persecution else None,
            result.rosfinmonitoring.status.value if result.rosfinmonitoring else None,
            result.review_required,
        )
        for result in via_graph.results
    }
    assert by_name == {
        "Альфа": ("political", "matched", False),
        "Бета": ("political", "not_matched", False),
        "Гамма": ("political", "ambiguous", True),
        "Дельта": ("political", "needs_review", True),
        "Эпсилон": ("political", "insufficient_data", True),
        "Дзета": ("political", "no_match_record", False),
        "Эта": ("uncertain", "not_matched", True),
    }

    # The product question: only a confirmed absence with a latest POLITICAL
    # classification qualifies — same answer with and without the graph.
    product = {"persecution_status": "political", "rosfinmonitoring_status": "not_matched"}
    direct_product = service.execute(
        ResearchRequest.model_validate(
            {"object_type": "person", "criteria": {**product, "snapshot_id": snapshot_id}}
        )
    )
    graph_product = run_research_query(
        build_research_graph(
            request_parser=FakeRequestParser(
                intake=ResearchIntake(request={"object_type": "person", "criteria": product})
            ),
            research_service=service,
            snapshot_lookup=SqlAlchemyRosfinmonitoringSnapshotLookup(session_factory),
            planner=ResearchPlanner(SOURCES),
        ),
        "Политически преследуемые, которых нет в перечне",
    )
    assert graph_product.results == direct_product.results
    assert [r.person.id for r in graph_product.results] == [people["Бета"]]


def test_report_over_postgres_has_real_citations_and_review_decisions(
    session_factory: sessionmaker[Session],
) -> None:
    """natural language -> fake parser -> graph -> real ResearchService -> ResearchReport."""
    with session_factory() as session:
        seed = ResearchSeeder(session)
        source_id = seed.source("ОВД-Инфо", "https://ovd.info")
        article_id, run_id = seed.article(
            source_id, external_id="report-1", title="Хроника", text=TEXT
        )
        snapshot_id = seed.snapshot("report")
        seed.entry(snapshot_id, "АЛЕКСЕЕВА АННА")
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
        seed.match(victor, snapshot_id, "not_matched", 0.8)
        seed.match(anna, snapshot_id, "ambiguous", 0.5)
        session.commit()

    parser = FakeRequestParser(
        intake=ResearchIntake(
            request={"object_type": "person", "criteria": {"persecution_status": "political"}}
        )
    )
    result = run_research_query(
        _graph(session_factory, parser), f"Политически преследуемые, snapshot {snapshot_id}"
    )

    assert result.status is WorkflowStatus.COMPLETED
    assert result.report is not None and result.plan is not None
    report = result.report
    assert report.status is ResearchReportStatus.REVIEW_REQUIRED
    items = {item.person_id: item for item in report.items}
    assert items[victor].review_required is False
    assert [reason.code for reason in items[anna].review.reasons] == [
        ResearchReviewReason.ROSFIN_AMBIGUOUS
    ]
    raw = {r.person.id: r for r in result.results}
    for person_id, item in items.items():
        assert item.citations
        for citation in item.citations:
            assert citation.article_id == article_id
            assert citation.url == "https://example.test/report-1"
            assert TEXT[citation.start_offset : citation.end_offset] == citation.text
        # Facts are copied from ResearchService, not re-derived.
        rf = raw[person_id].rosfinmonitoring
        assert rf is not None and item.rosfinmonitoring_status is rf.status
    (event_claim,) = [c for c in items[victor].claims if c.event_id is not None]
    assert [c.text for c in event_claim.citations] == ["Суд арестовал Виктора Викторова"]
    assert report.source_refresh_recommended is False


def test_report_for_empty_database_result_recommends_refresh(
    session_factory: sessionmaker[Session],
) -> None:
    parser = FakeRequestParser(
        intake=ResearchIntake(request={"object_type": "person", "criteria": {"name": "Никто"}})
    )

    result = run_research_query(_graph(session_factory, parser), "Найди Никто")

    assert result.status is WorkflowStatus.COMPLETED
    assert result.report is not None
    assert result.report.status is ResearchReportStatus.INSUFFICIENT_DATA
    assert set(result.report.recommended_sources) == set(SOURCES)


def test_empty_accepted_candidates_never_turn_into_an_unrestricted_search(
    session_factory: sessionmaker[Session],
) -> None:
    """Regression: candidate_person_ids=[] means "no semantic candidates", None means
    "no restriction". Persons exist; the semantic request must still return nobody."""
    with session_factory() as session:
        seed = ResearchSeeder(session)
        for name in ("Иван Иванов", "Пётр Петров"):
            person_id = seed.person(name)
            seed.classification(person_id, "political", 0.9)
        session.commit()
    service = ResearchService(
        repository=SqlAlchemyPersonResearchRepository(session_factory),
        candidate_query=CandidateQueryService(session_factory),
    )
    structured = ResearchRequest.model_validate(
        {"object_type": "person", "criteria": {"persecution_status": "political"}}
    )
    semantic = ResearchRequest.model_validate(
        {
            "object_type": "person",
            "criteria": {
                "persecution_status": "political",
                "semantic_query": "выращивание бананов на Марсе",
            },
        }
    )

    assert service.execute(structured, candidate_person_ids=None).total_matched == 2
    empty = service.execute(semantic, candidate_person_ids=[])
    assert (empty.results, empty.total_matched) == ([], 0)
