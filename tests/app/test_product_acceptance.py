"""Product acceptance (ADR 0014): the main workflow end to end, without manual SQL.

fixture source → monitoring (ingest, extraction, ER v2, classification, RF,
findings) → the persons persecuted for politics and absent from the list.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from support.monitoring_fixtures import (
    SIDOROV,
    FakeUpstream,
    build_service,
    import_rf_snapshot,
    table_counts,
)
from support.person_resolution_fixtures import seed_person

from candidates.service import CandidateQueryService
from db.orm_models import MonitoringFindingRecord, PersonResolutionDecisionRecord
from monitoring.models import MonitoringRunStatus
from persons.persistence import SqlAlchemyPersonPersistence
from persons.resolution.review import PersonResolutionReviewService, ResolutionReviewAction

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


def _not_in_rf(session_factory: sessionmaker[Session], snapshot_id: int) -> list[tuple[int, str]]:
    result = CandidateQueryService(session_factory).get_candidates(snapshot_id, limit=None)
    return [(candidate.person_id, candidate.canonical_name) for candidate in result.candidates]


def test_new_publication_becomes_a_finding_and_a_person_not_in_the_list(
    session_factory: sessionmaker[Session],
) -> None:
    snapshot_id = import_rf_snapshot(session_factory, [("Петр Петров", "03.03.1970")])
    upstream = FakeUpstream()
    upstream.publish("sidorov", SIDOROV)
    service = build_service(session_factory, {"ovd-info": upstream})

    first = service.run_source("ovd-info")
    candidates_one = _not_in_rf(session_factory, snapshot_id)
    counts = table_counts(session_factory, *DOMAIN_TABLES)
    second = service.run_source("ovd-info")

    assert first.status is MonitoringRunStatus.COMPLETED
    assert first.findings_created == 1
    [(person_id, name)] = candidates_one
    assert name == "Сергей Сидоров"
    with session_factory() as session:
        finding = session.scalars(select(MonitoringFindingRecord)).one()
    assert finding.person_id == person_id
    assert finding.first_seen_run_id == first.id

    # Repeated run: no duplicates of any kind, the same persons.
    assert second.status is MonitoringRunStatus.COMPLETED
    assert (second.documents_ingested, second.findings_created) == (0, 0)
    assert table_counts(session_factory, *DOMAIN_TABLES) == counts
    assert _not_in_rf(session_factory, snapshot_id) == candidates_one


def test_review_path_keeps_the_candidate_out_until_a_human_decides(
    session_factory: sessionmaker[Session],
) -> None:
    snapshot_id = import_rf_snapshot(session_factory, [("Петр Петров", "03.03.1970")])
    seed_person(session_factory, "Сергей Сидоров")
    seed_person(session_factory, "Сергей Сидоров")
    upstream = FakeUpstream()
    upstream.publish("sidorov", SIDOROV)
    service = build_service(session_factory, {"ovd-info": upstream})

    run = service.run_source("ovd-info")

    assert run.status is MonitoringRunStatus.COMPLETED
    assert run.person_reviews_created == 1
    assert table_counts(session_factory, "monitoring_findings")["monitoring_findings"] == 0
    assert _not_in_rf(session_factory, snapshot_id) == []

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

    assert derived.status is MonitoringRunStatus.COMPLETED
    assert derived.findings_created == 1
    assert upstream.fetches == ["sidorov"]
    assert [person_id for person_id, _ in _not_in_rf(session_factory, snapshot_id)] == [
        created.person_id
    ]


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
