"""Monitoring pipeline over real domain services on PostgreSQL with fake upstream sources."""

from __future__ import annotations

import threading
import time
from datetime import timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker
from support.monitoring_fixtures import (
    IVANOV,
    MALFORMED_MARKER,
    PETROV,
    SIDOROV,
    FakeUpstream,
    build_service,
    import_rf_snapshot,
    semantic_indexer,
    table_counts,
)
from support.person_resolution_fixtures import seed_person
from support.semantic_fakes import UnavailableStore

from db.orm_models import (
    ArticleExtractionRunRecord,
    EntityMentionRecord,
    MonitoringFindingRecord,
    PersecutionClassificationRecord,
    PersonRecord,
    PersonResolutionDecisionRecord,
    RosfinMatchRecord,
    SemanticDocumentRecord,
)
from monitoring.findings import ENBV_CRITERIA_VERSION, NO_RF_SNAPSHOT, POLITICAL_NOT_IN_RF
from monitoring.models import (
    FailureKind,
    MonitoringAlreadyRunningError,
    MonitoringRunAbortedError,
    MonitoringRunStatus,
    MonitoringStage,
    MonitoringTrigger,
)
from monitoring.selection import EVIDENCE_SETTLE_INTERVAL, SqlAlchemyMonitoringWorkQueries
from persecution.classifier import RuleBasedPersecutionClassifier
from persons.persistence import SqlAlchemyPersonPersistence
from persons.resolution.review import PersonResolutionReviewService, ResolutionReviewAction
from persons.resolution.service import RESOLVER_VERSION
from rosfinmonitoring.matcher import RuleBasedRosfinmonitoringMatcher
from sources.ingestion_errors import TransientDiscoveryError

DOMAIN_TABLES = (
    "source_documents",
    "parsed_articles",
    "article_extraction_runs",
    "entity_mentions",
    "extracted_events",
    "persons",
    "person_event_links",
    "person_resolution_decisions",
    "review_records",
    "persecution_classifications",
    "rosfin_matches",
    "semantic_documents",
    "monitoring_findings",
)


def _person_id(session_factory: sessionmaker[Session], name: str) -> int:
    with session_factory() as session:
        person_id = session.scalar(
            select(PersonRecord.id).where(PersonRecord.canonical_name.ilike(f"%{name}%"))
        )
    assert person_id is not None, f"{name} was not resolved to a person"
    return person_id


def test_repeated_run_with_same_upstream_creates_no_duplicates(
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
    counts_after_first = table_counts(session_factory, *DOMAIN_TABLES)
    second = service.run_source("ovd-info")

    assert first.status is MonitoringRunStatus.COMPLETED
    assert (first.documents_discovered, first.documents_ingested, first.articles_extracted) == (
        1,
        1,
        1,
    )
    assert first.persons_created == 1
    assert first.events_created == 1
    assert first.classifications_created == 1
    assert first.rf_matches_created == 1
    assert first.semantic_entities_indexed >= 2
    assert first.findings_created == 1
    assert counts_after_first["parsed_articles"] == 1
    assert counts_after_first["persons"] == 1
    assert counts_after_first["extracted_events"] == 1
    assert counts_after_first["persecution_classifications"] == 1
    assert counts_after_first["monitoring_findings"] == 1

    assert second.status is MonitoringRunStatus.COMPLETED
    assert (second.documents_discovered, second.documents_skipped, second.documents_ingested) == (
        1,
        1,
        0,
    )
    assert second.articles_extracted == 0
    assert second.persons_created == second.persons_linked == 0
    assert second.classifications_created == second.rf_matches_created == 0
    assert second.semantic_entities_indexed == 0
    assert second.findings_created == 0
    assert table_counts(session_factory, *DOMAIN_TABLES) == counts_after_first
    assert upstream.fetches == ["sidorov"]


def test_second_run_processes_only_the_new_article(session_factory: sessionmaker[Session]) -> None:
    upstream = FakeUpstream()
    upstream.publish("sidorov", SIDOROV)
    service = build_service(session_factory, {"ovd-info": upstream})
    service.run_source("ovd-info")

    upstream.publish("petrov", PETROV)
    second = service.run_source("ovd-info")

    assert second.status is MonitoringRunStatus.COMPLETED
    assert (second.documents_discovered, second.documents_skipped, second.documents_ingested) == (
        2,
        1,
        1,
    )
    assert second.articles_extracted == 1
    assert second.persons_created == 1
    assert second.classifications_created == 1
    assert upstream.fetches == ["sidorov", "petrov"]
    assert table_counts(session_factory, "parsed_articles")["parsed_articles"] == 2


def test_er_review_is_an_outcome_not_a_failure(session_factory: sessionmaker[Session]) -> None:
    seed_person(session_factory, "Сергей Сидоров")
    seed_person(session_factory, "Сергей Сидоров")
    upstream = FakeUpstream()
    upstream.publish("sidorov", SIDOROV)
    upstream.publish("petrov", PETROV)
    service = build_service(session_factory, {"ovd-info": upstream})

    run = service.run_source("ovd-info")

    assert run.status is MonitoringRunStatus.COMPLETED
    assert run.error_count == 0
    assert run.person_reviews_created == 1
    # The other mention of the same run is still resolved.
    assert run.persons_created == 1
    with session_factory() as session:
        pending = session.scalars(
            select(PersonResolutionDecisionRecord).where(
                PersonResolutionDecisionRecord.status == "pending_review"
            )
        ).all()
    assert len(pending) == 1
    with session_factory() as session:
        mention = session.get(EntityMentionRecord, pending[0].mention_id)
    assert mention is not None
    assert mention.person_id is None


def test_extraction_failure_of_one_article_does_not_stop_others(
    session_factory: sessionmaker[Session],
) -> None:
    upstream = FakeUpstream()
    upstream.publish("a", SIDOROV)
    upstream.publish("b", f"{MALFORMED_MARKER} {PETROV}")
    upstream.publish("c", IVANOV)
    service = build_service(session_factory, {"ovd-info": upstream})

    run = service.run_source("ovd-info")

    assert run.status is MonitoringRunStatus.COMPLETED_WITH_ERRORS
    assert run.documents_ingested == 3
    assert run.articles_extracted == 2
    assert run.persons_created == 2
    details = service._deps.repository.get_run_details(run.id)
    assert details is not None
    assert [(item.stage, item.entity_type, item.failure_kind) for item in details.items] == [
        (MonitoringStage.EXTRACTION, "article", FailureKind.NON_RETRYABLE)
    ]
    assert "malformed markup" in details.items[0].error_message

    # The deterministic failure is not retried on every run.
    again = service.run_source("ovd-info")
    assert again.status is MonitoringRunStatus.COMPLETED
    assert again.error_count == 0


def test_qdrant_outage_keeps_postgres_work_and_semantic_retry_needs_no_reingestion(
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

    failed = outage.run_source("ovd-info")

    assert failed.status is MonitoringRunStatus.COMPLETED_WITH_ERRORS
    assert failed.stage_metrics["semantic_indexing"]["status"] == "failed"
    assert failed.stage_metrics["semantic_indexing"]["failure_kind"] == "retryable"
    assert failed.semantic_entities_indexed == 0
    assert failed.findings_created == 1
    domain = table_counts(
        session_factory,
        "parsed_articles",
        "persons",
        "persecution_classifications",
        "rosfin_matches",
    )
    assert domain == {
        "parsed_articles": 1,
        "persons": 1,
        "persecution_classifications": 1,
        "rosfin_matches": 1,
    }
    with session_factory() as session:
        runs_before = session.scalars(select(ArticleExtractionRunRecord.id)).all()

    recovered = build_service(
        session_factory,
        {"ovd-info": upstream},
        create_semantic_indexer=lambda: semantic_indexer(session_factory),
    )
    retry = recovered.run_derived()

    assert retry.status is MonitoringRunStatus.COMPLETED
    assert retry.trigger_type is MonitoringTrigger.DERIVED
    assert retry.source is None
    assert retry.documents_discovered == 0
    assert retry.semantic_entities_indexed == 2
    assert retry.classifications_created == 0
    assert upstream.fetches == ["sidorov"]
    assert upstream.discoveries == 1
    with session_factory() as session:
        assert session.scalars(select(ArticleExtractionRunRecord.id)).all() == runs_before
        indexed = session.scalars(
            select(SemanticDocumentRecord.indexed_at).where(
                SemanticDocumentRecord.indexed_at.is_not(None)
            )
        ).all()
    assert len(indexed) == 2
    assert table_counts(session_factory, *domain) == domain


def test_same_source_cannot_start_while_running(session_factory: sessionmaker[Session]) -> None:
    upstream = FakeUpstream()
    upstream.publish("sidorov", SIDOROV)
    service = build_service(session_factory, {"ovd-info": upstream})
    handle = service.start_source_run("ovd-info")

    with pytest.raises(MonitoringAlreadyRunningError):
        service.run_source("ovd-info")

    assert upstream.discoveries == 0
    service.finish(handle)


def test_different_sources_run_in_parallel(session_factory: sessionmaker[Session]) -> None:
    ovd = FakeUpstream()
    ovd.publish("sidorov", SIDOROV)
    sota = FakeUpstream()
    sota.publish("ivanov", IVANOV)
    service = build_service(session_factory, {"ovd-info": ovd, "sota-vision": sota})
    barrier = threading.Barrier(2)
    statuses: dict[str, MonitoringRunStatus] = {}

    def run(source: str) -> None:
        barrier.wait()
        statuses[source] = service.run_source(source).status

    threads = [threading.Thread(target=run, args=(name,)) for name in ("ovd-info", "sota-vision")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert statuses == {
        "ovd-info": MonitoringRunStatus.COMPLETED,
        "sota-vision": MonitoringRunStatus.COMPLETED,
    }
    counts = table_counts(session_factory, "persons", "persecution_classifications")
    assert counts == {"persons": 2, "persecution_classifications": 2}


def test_run_after_crash_aborts_stale_run_and_finishes_its_work(
    session_factory: sessionmaker[Session],
) -> None:
    upstream = FakeUpstream()
    upstream.publish("sidorov", SIDOROV)
    service = build_service(session_factory, {"ovd-info": upstream})
    crashed = service.start_source_run("ovd-info")
    service.ingest(crashed, service.discover(crashed))  # process dies before extraction
    with session_factory.begin() as session:
        session.execute(
            text(
                "UPDATE monitoring_runs SET heartbeat_at = now() - interval '3 hours' "
                "WHERE id = :id"
            ),
            {"id": crashed.run_id},
        )

    run = service.run_source("ovd-info")

    stale = service._deps.repository.get_run(crashed.run_id)
    assert stale is not None
    assert stale.status is MonitoringRunStatus.ABORTED
    assert run.status is MonitoringRunStatus.COMPLETED
    assert run.documents_skipped == 1
    assert run.documents_ingested == 0
    assert run.articles_extracted == 1
    assert run.persons_created == 1
    assert upstream.fetches == ["sidorov"]


def test_political_person_absent_from_rf_becomes_one_finding(
    session_factory: sessionmaker[Session],
) -> None:
    snapshot_id = import_rf_snapshot(session_factory, [("Петр Петров", "03.03.1970")])
    upstream = FakeUpstream()
    upstream.publish("sidorov", SIDOROV)
    upstream.publish("petrov", PETROV)
    service = build_service(session_factory, {"ovd-info": upstream})

    first = service.run_source("ovd-info")
    upstream.publish("ivanov-unrelated", PETROV.replace("Петра Петрова", "Павла Павлова"))
    second = service.run_source("ovd-info")

    sidorov = _person_id(session_factory, "Сидоров")
    with session_factory() as session:
        findings = session.scalars(select(MonitoringFindingRecord)).all()
        classification_id = session.scalar(
            select(PersecutionClassificationRecord.id).where(
                PersecutionClassificationRecord.person_id == sidorov
            )
        )
        match_id = session.scalar(
            select(RosfinMatchRecord.id).where(RosfinMatchRecord.person_id == sidorov)
        )
    assert first.findings_created == 1
    assert second.findings_created == 0
    assert len(findings) == 1
    finding = findings[0]
    assert (finding.finding_type, finding.criteria_version) == (
        POLITICAL_NOT_IN_RF,
        ENBV_CRITERIA_VERSION,
    )
    assert finding.person_id == sidorov
    assert finding.active is True
    assert finding.first_seen_run_id == first.id
    assert finding.last_seen_run_id == second.id
    assert finding.snapshot_id == snapshot_id
    assert finding.persecution_classification_id == classification_id
    assert finding.rosfin_match_id == match_id


def test_new_evidence_for_a_known_person_reclassifies_and_creates_a_finding(
    session_factory: sessionmaker[Session],
) -> None:
    import_rf_snapshot(session_factory, [("Иван Иванов", "01.01.1980")])
    upstream = FakeUpstream()
    upstream.publish("petrov-hooligan", PETROV)
    service = build_service(
        session_factory,
        {"ovd-info": upstream},
        create_semantic_indexer=lambda: semantic_indexer(session_factory),
    )
    first = service.run_source("ovd-info")
    petrov = _person_id(session_factory, "Петров")

    upstream.publish(
        "petrov-political",
        "Петр Петров, активист, задержан на антивоенном митинге. "
        "Правозащитники считают дело политически мотивированным.",
    )
    second = service.run_source("ovd-info")

    assert first.stage_metrics["classification"]["statuses"] == {"non_political": 1}
    assert first.findings_created == 0
    assert second.persons_created == 0
    assert second.persons_linked == 1
    assert second.classifications_created == 1
    assert second.stage_metrics["classification"]["statuses"] == {"political": 1}
    assert second.rf_matches_created == 1
    assert second.findings_created == 1
    # The already indexed person document is outdated by the new evidence and re-embedded.
    assert second.stage_metrics["semantic_indexing"]["pending_persons"] == 1
    assert second.stage_metrics["semantic_indexing"]["pending_events"] == 1
    assert second.semantic_entities_indexed == 2
    with session_factory() as session:
        assert session.scalars(select(MonitoringFindingRecord.person_id)).all() == [petrov]


def test_rf_ambiguous_person_is_not_reported_as_absent(
    session_factory: sessionmaker[Session],
) -> None:
    import_rf_snapshot(
        session_factory, [("Сергей Сидоров", "01.01.1980"), ("Сергей Сидоров", "02.02.1990")]
    )
    upstream = FakeUpstream()
    upstream.publish("sidorov", SIDOROV)
    service = build_service(session_factory, {"ovd-info": upstream})

    run = service.run_source("ovd-info")

    assert run.status is MonitoringRunStatus.COMPLETED
    assert run.stage_metrics["rf_matching"]["statuses"] == {"ambiguous": 1}
    assert run.findings_created == 0
    assert table_counts(session_factory, "monitoring_findings")["monitoring_findings"] == 0


def test_no_rf_snapshot_skips_matching_and_findings(session_factory: sessionmaker[Session]) -> None:
    upstream = FakeUpstream()
    upstream.publish("sidorov", SIDOROV)
    service = build_service(session_factory, {"ovd-info": upstream})

    run = service.run_source("ovd-info")

    assert run.status is MonitoringRunStatus.COMPLETED
    assert run.rf_snapshot_id is None
    assert run.stage_metrics["rf_matching"]["skipped"] == NO_RF_SNAPSHOT
    assert run.stage_metrics["findings"]["skipped"] == NO_RF_SNAPSHOT
    assert run.classifications_created == 1
    assert table_counts(session_factory, "rosfin_matches", "monitoring_findings") == {
        "rosfin_matches": 0,
        "monitoring_findings": 0,
    }


def test_finding_is_deactivated_not_deleted_when_person_leaves_the_criterion(
    session_factory: sessionmaker[Session],
) -> None:
    import_rf_snapshot(session_factory, [("Петр Петров", "03.03.1970")])
    upstream = FakeUpstream()
    upstream.publish("sidorov", SIDOROV)
    service = build_service(session_factory, {"ovd-info": upstream})
    first = service.run_source("ovd-info")
    # Sidorov appears in a newer snapshot: no longer "absent from RF".
    import_rf_snapshot(session_factory, [("Сергей Сидоров", "01.01.1980")])

    later = service.run_derived()

    with session_factory() as session:
        finding = session.scalars(select(MonitoringFindingRecord)).one()
    assert later.rf_matches_created == 1
    assert later.stage_metrics["findings"]["criteria"][0]["deactivated"] == 1
    assert finding.active is False
    assert finding.inactive_since is not None
    assert finding.first_seen_run_id == first.id


def test_person_created_by_review_is_processed_without_reingestion(
    session_factory: sessionmaker[Session],
) -> None:
    import_rf_snapshot(session_factory, [("Петр Петров", "03.03.1970")])
    seed_person(session_factory, "Сергей Сидоров")
    seed_person(session_factory, "Сергей Сидоров")
    upstream = FakeUpstream()
    upstream.publish("sidorov", SIDOROV)
    service = build_service(
        session_factory,
        {"ovd-info": upstream},
        create_semantic_indexer=lambda: semantic_indexer(session_factory),
    )
    service.run_source("ovd-info")
    with session_factory.begin() as session:
        decision_id = session.scalar(
            select(PersonResolutionDecisionRecord.id).where(
                PersonResolutionDecisionRecord.status == "pending_review",
                PersonResolutionDecisionRecord.resolver_version == RESOLVER_VERSION,
            )
        )
        assert decision_id is not None
        result = PersonResolutionReviewService(SqlAlchemyPersonPersistence(session_factory)).apply(
            session, decision_id, ResolutionReviewAction.CREATE_NEW_PERSON
        )
    new_person_id = result.person_id
    assert new_person_id is not None

    derived = service.run_derived()

    assert derived.status is MonitoringRunStatus.COMPLETED
    assert derived.documents_discovered == 0
    assert upstream.fetches == ["sidorov"]
    with session_factory() as session:
        assert (
            session.scalar(
                select(PersecutionClassificationRecord.status).where(
                    PersecutionClassificationRecord.person_id == new_person_id
                )
            )
            == "political"
        )
        assert session.scalar(
            select(RosfinMatchRecord.status).where(RosfinMatchRecord.person_id == new_person_id)
        ) == ("not_matched")
        assert session.scalar(
            select(SemanticDocumentRecord.indexed_at).where(
                SemanticDocumentRecord.entity_type == "person",
                SemanticDocumentRecord.entity_id == new_person_id,
            )
        )
        finding_person_ids = session.scalars(select(MonitoringFindingRecord.person_id)).all()
    assert new_person_id in finding_person_ids


def test_backfill_and_dry_run_never_move_the_checkpoint(
    session_factory: sessionmaker[Session],
) -> None:
    upstream = FakeUpstream()
    for index in range(3):
        upstream.publish(f"petrov-{index}", PETROV)
    service = build_service(session_factory, {"ovd-info": upstream}, discovery_limit=1)

    plan = service.dry_run("ovd-info", discovery_limit=3)
    assert (plan.discovered, plan.estimated_new) == (3, 3)
    assert table_counts(session_factory, "source_documents", "monitoring_runs") == {
        "source_documents": 0,
        "monitoring_runs": 0,
    }

    regular = service.run_source("ovd-info")
    backfill = service.run_source("ovd-info", trigger=MonitoringTrigger.BACKFILL, discovery_limit=3)

    states = service.status().sources
    assert regular.documents_discovered == 1
    assert backfill.documents_ingested == 2
    assert [(state.source_name, state.last_successful_run_id) for state in states] == [
        ("ovd-info", regular.id)
    ]
    assert service.dry_run("ovd-info", discovery_limit=3).estimated_new == 0


def test_backfill_can_refetch_known_documents(session_factory: sessionmaker[Session]) -> None:
    upstream = FakeUpstream()
    upstream.publish("sidorov", SIDOROV)
    service = build_service(session_factory, {"ovd-info": upstream})
    service.run_source("ovd-info")

    with pytest.raises(ValueError, match="backfill"):
        service.run_source("ovd-info", refetch_known=True)
    backfill = service.run_source(
        "ovd-info", trigger=MonitoringTrigger.BACKFILL, refetch_known=True
    )

    assert backfill.documents_ingested == 1
    assert backfill.documents_skipped == 0
    assert backfill.articles_extracted == 0  # same content: extraction is idempotent
    assert upstream.fetches == ["sidorov", "sidorov"]


def test_discovery_failure_fails_the_run_and_keeps_the_checkpoint(
    session_factory: sessionmaker[Session],
) -> None:
    upstream = FakeUpstream()
    upstream.publish("sidorov", SIDOROV)
    service = build_service(session_factory, {"ovd-info": upstream})
    completed = service.run_source("ovd-info")
    upstream.discovery_error = TransientDiscoveryError("Temporary discovery failure: HTTP 503")

    failed = service.run_source("ovd-info")

    assert failed.status is MonitoringRunStatus.FAILED
    assert failed.error_message == "TransientDiscoveryError: Temporary discovery failure: HTTP 503"
    assert [
        (state.source_name, state.last_successful_run_id) for state in service.status().sources
    ] == [("ovd-info", completed.id)]
    # A failed run releases the source: the next run can start.
    upstream.discovery_error = None
    assert service.run_source("ovd-info").status is MonitoringRunStatus.COMPLETED


def test_stale_timeout_is_configured_not_hardcoded(session_factory: sessionmaker[Session]) -> None:
    upstream = FakeUpstream()
    service = build_service(session_factory, {"ovd-info": upstream})
    assert service.settings.stale_run_after == timedelta(minutes=120)


def test_result_written_during_an_open_evidence_transaction_is_recomputed(
    session_factory: sessionmaker[Session],
) -> None:
    """A classification/match computed while another run's ER transaction was open is
    written after that transaction's `now()` yet without its rows; it must stay pending."""
    snapshot_id = import_rf_snapshot(session_factory, [("Петр Петров", "03.03.1970")])
    upstream = FakeUpstream()
    upstream.publish("sidorov", SIDOROV)
    build_service(session_factory, {"ovd-info": upstream}).run_source("ovd-info")
    sidorov = _person_id(session_factory, "Сидоров")
    evidence_at = "now() - interval '1 day'"

    def written_after_evidence(delay: str) -> None:
        with session_factory.begin() as session:
            for table, column in (
                ("persons", "id"),
                ("person_event_links", "person_id"),
                ("person_aliases", "person_id"),
                ("person_resolution_decisions", "selected_person_id"),
            ):
                session.execute(
                    text(f"UPDATE {table} SET created_at = {evidence_at} WHERE {column} = :id"),
                    {"id": sidorov},
                )
            session.execute(
                text(f"UPDATE persons SET updated_at = {evidence_at} WHERE id = :id"),
                {"id": sidorov},
            )
            session.execute(
                text(
                    f"UPDATE persecution_classifications SET classified_at = {evidence_at} "
                    f"+ interval '{delay}' WHERE person_id = :id"
                ),
                {"id": sidorov},
            )
            session.execute(
                text(
                    f"UPDATE rosfin_matches SET matched_at = {evidence_at} + interval '{delay}' "
                    "WHERE person_id = :id"
                ),
                {"id": sidorov},
            )

    classifier = (
        RuleBasedPersecutionClassifier.classifier_name,
        RuleBasedPersecutionClassifier.classifier_version,
    )
    matcher = {
        "matcher_name": RuleBasedRosfinmonitoringMatcher.matcher_name,
        "matcher_version": RuleBasedRosfinmonitoringMatcher.matcher_version,
    }
    no_settle = SqlAlchemyMonitoringWorkQueries(
        session_factory, evidence_settle_interval=timedelta(0)
    )
    production = SqlAlchemyMonitoringWorkQueries(session_factory)

    written_after_evidence("1 minute")
    assert (
        no_settle.persons_pending_classification(
            classifier_name=classifier[0], classifier_version=classifier[1]
        )
        == []
    )
    assert production.persons_pending_classification(
        classifier_name=classifier[0], classifier_version=classifier[1]
    ) == [sidorov]
    assert production.persons_pending_rf_match(snapshot_id=snapshot_id, **matcher) == [sidorov]

    written_after_evidence(f"{int(EVIDENCE_SETTLE_INTERVAL.total_seconds() // 60) + 1} minutes")
    assert (
        production.persons_pending_classification(
            classifier_name=classifier[0], classifier_version=classifier[1]
        )
        == []
    )
    assert production.persons_pending_rf_match(snapshot_id=snapshot_id, **matcher) == []


def test_rf_match_of_an_older_matcher_version_is_recomputed(
    session_factory: sessionmaker[Session],
) -> None:
    """A matcher rule change must reach persisted results: an old NOT_MATCHED must not
    keep saying "absent from the list" when neither the person nor the snapshot changed."""
    snapshot_id = import_rf_snapshot(session_factory, [("Петр Петров", "03.03.1970")])
    upstream = FakeUpstream()
    upstream.publish("sidorov", SIDOROV)
    service = build_service(session_factory, {"ovd-info": upstream})
    service.run_source("ovd-info")
    sidorov = _person_id(session_factory, "Сидоров")
    work = SqlAlchemyMonitoringWorkQueries(session_factory, evidence_settle_interval=timedelta(0))
    current = (
        RuleBasedRosfinmonitoringMatcher.matcher_name,
        RuleBasedRosfinmonitoringMatcher.matcher_version,
    )

    def pending() -> list[int]:
        return work.persons_pending_rf_match(
            snapshot_id=snapshot_id, matcher_name=current[0], matcher_version=current[1]
        )

    with session_factory() as session:
        match = session.scalars(
            select(RosfinMatchRecord).where(RosfinMatchRecord.person_id == sidorov)
        ).one()
    assert (match.matcher_name, match.matcher_version) == current
    assert pending() == []

    for old_version in ("1.0.0", None):
        with session_factory.begin() as session:
            session.execute(
                text(
                    "UPDATE rosfin_matches SET matcher_version = :version, "
                    "reasons = '[\"stale\"]' WHERE person_id = :id"
                ),
                {"version": old_version, "id": sidorov},
            )
        assert pending() == [sidorov]

    service.run_derived()

    with session_factory() as session:
        match = session.scalars(
            select(RosfinMatchRecord).where(RosfinMatchRecord.person_id == sidorov)
        ).one()
    assert (match.matcher_name, match.matcher_version) == current
    assert match.reasons != ["stale"]
    assert pending() == []


def test_worker_of_an_aborted_run_stops_before_writing_domain_rows(
    session_factory: sessionmaker[Session],
) -> None:
    upstream = FakeUpstream()
    upstream.publish("sidorov", SIDOROV)
    service = build_service(session_factory, {"ovd-info": upstream})
    zombie = service.start_source_run("ovd-info")
    ingestion = service.ingest(zombie, service.discover(zombie))
    with session_factory.begin() as session:
        session.execute(
            text("UPDATE monitoring_runs SET heartbeat_at = now() - interval '3 hours'")
        )
    service.repository.abort_stale_runs(service.settings.stale_run_after)

    with pytest.raises(MonitoringRunAbortedError):
        service.extract(zombie, ingestion)
    # A new stage is refused before any upstream call.
    with pytest.raises(MonitoringRunAbortedError):
        service.discover(zombie)
    assert upstream.discoveries == 1

    assert table_counts(session_factory, "article_extraction_runs") == {
        "article_extraction_runs": 0
    }
    view = service.finish(zombie)
    assert view.status is MonitoringRunStatus.ABORTED
    assert service.status().sources == []


def test_run_waiting_for_a_derived_lock_keeps_its_heartbeat(
    session_factory: sessionmaker[Session],
) -> None:
    upstream = FakeUpstream()
    upstream.publish("sidorov", SIDOROV)
    service = build_service(
        session_factory, {"ovd-info": upstream}, stale_run_after=timedelta(seconds=2)
    )
    engine = session_factory.kw["bind"]
    statuses: list[MonitoringRunStatus] = []
    with engine.connect() as holder:
        holder.execute(
            text(
                "SELECT pg_advisory_lock(hashtextextended('monitoring:derived:classification', 0))"
            )
        )
        holder.commit()
        worker = threading.Thread(
            target=lambda: statuses.append(service.run_source("ovd-info").status)
        )
        worker.start()
        time.sleep(4)  # twice the stale timeout, spent waiting for the lock

        with pytest.raises(MonitoringAlreadyRunningError):
            service.start_source_run("ovd-info")

        holder.execute(
            text(
                "SELECT pg_advisory_unlock(hashtextextended('monitoring:derived:classification', 0))"
            )
        )
        holder.commit()
    worker.join(timeout=30)

    assert statuses == [MonitoringRunStatus.COMPLETED]


def test_reappearing_person_reactivates_the_same_finding(
    session_factory: sessionmaker[Session],
) -> None:
    import_rf_snapshot(session_factory, [("Петр Петров", "03.03.1970")])
    upstream = FakeUpstream()
    upstream.publish("sidorov", SIDOROV)
    service = build_service(session_factory, {"ovd-info": upstream})
    first = service.run_source("ovd-info")
    import_rf_snapshot(session_factory, [("Сергей Сидоров", "01.01.1980")])
    service.run_derived()
    import_rf_snapshot(session_factory, [("Иван Иванов", "02.02.1990")])

    again = service.run_derived()

    [criterion] = again.stage_metrics["findings"]["criteria"]
    assert (criterion["created"], criterion["reactivated"], criterion["deactivated"]) == (0, 1, 0)
    assert again.findings_created == 0
    with session_factory() as session:
        finding = session.scalars(select(MonitoringFindingRecord)).one()
    assert finding.active is True
    assert finding.inactive_since is None
    assert finding.first_seen_run_id == first.id
