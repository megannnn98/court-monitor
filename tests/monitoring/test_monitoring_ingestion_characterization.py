"""Characterization of the monitoring ingestion stage, as Dagster drives it:
`start_source_run -> discover -> ingest -> finish`, on PostgreSQL with a fake upstream.

Pins which references are fetched and in what order, what a known document, a post
without text, a fetch or parse failure and the published-date window each do, the
run's counters, stage metrics and failure items, the stored documents and the
checkpoint. A persistence failure is pinned separately: it stops the stage.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from support.monitoring_fixtures import (
    MEDIA_ONLY,
    FakeArticleParser,
    FakeSourceAdapter,
    FakeUpstream,
    UnusedFetcher,
)

from application import build_monitoring_service
from db.orm_models import ParsedArticleRecord, Source, SourceDocument
from extraction.events import RuleBasedEventExtractor
from extraction.extractors import RuleBasedEntityExtractor
from extraction.normalizers import RuleBasedMentionNormalizer
from extraction.persistence import SqlAlchemyExtractionPersistence
from extraction.pipeline import ExtractionPipeline
from monitor_core.errors import PermanentFetchError, PersistenceError, TransientFetchError
from monitor_core.model import ParsedArticle, PersistenceResult, RawDocument, SourceReference
from monitor_core.ports import DocumentFetcher, SourceAdapter
from monitoring.models import (
    FailureKind,
    MonitoringRunStatus,
    MonitoringSettings,
    MonitoringStage,
    MonitoringTrigger,
)
from monitoring.service import DiscoveryResult, IngestionResult, MonitoringService
from sources.source_registry import SourceDefinition
from sources.sqlalchemy_persistence import SqlAlchemyIngestionPersistence

# Settings accept only registered names; the URLs stay fake.
SOURCE = "ovd-info"
IN_RANGE = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)
BEFORE_RANGE = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)


class ScriptedAdapter(FakeSourceAdapter):
    """The fixture adapter, with fetch failures scripted per external id."""

    def __init__(self, upstream: FakeUpstream, fetch_errors: dict[str, Exception]) -> None:
        super().__init__(SOURCE, upstream)
        self._upstream_ref = upstream
        self._fetch_errors = fetch_errors

    async def fetch(self, reference: SourceReference) -> RawDocument:
        error = self._fetch_errors.get(reference.external_id)
        if error is not None:
            self._upstream_ref.fetches.append(reference.external_id)
            raise error
        return await super().fetch(reference)


def _service(
    session_factory: sessionmaker[Session],
    upstream: FakeUpstream,
    fetch_errors: dict[str, Exception],
) -> MonitoringService:
    def create_adapter(_client: object, _fetcher: DocumentFetcher) -> SourceAdapter:
        return ScriptedAdapter(upstream, fetch_errors)

    definition = SourceDefinition(
        name=SOURCE,
        source_name="Characterization",
        base_url=f"https://{SOURCE}.test",
        create_adapter=create_adapter,
        create_parser=FakeArticleParser,
    )
    return build_monitoring_service(
        session_factory,
        settings=MonitoringSettings(enabled_sources=(SOURCE,), discovery_limit=20),
        env={},
        sources={SOURCE: definition},
        create_fetcher=UnusedFetcher,
        extraction_pipeline=ExtractionPipeline(
            extractors=[RuleBasedEntityExtractor()],
            normalizers=[RuleBasedMentionNormalizer()],
            event_extractor=RuleBasedEventExtractor(),
            persistence=SqlAlchemyExtractionPersistence(session_factory),
        ),
    )


def _stored(session_factory: sessionmaker[Session]) -> list[tuple[str, str, datetime | None]]:
    with session_factory() as session:
        rows = session.execute(
            select(
                SourceDocument.external_id,
                ParsedArticleRecord.title,
                ParsedArticleRecord.published_at,
            )
            .join(ParsedArticleRecord, ParsedArticleRecord.document_id == SourceDocument.id)
            .join(Source, Source.id == SourceDocument.source_id)
            .where(Source.base_url == f"https://{SOURCE}.test")
            .order_by(SourceDocument.id)
        ).all()
    return [(row[0], row[1], row[2]) for row in rows]


def _offset_document_ids(session_factory: sessionmaker[Session]) -> None:
    """A document of another source without a parsed article: document and article ids
    then differ, so a run handing on the wrong one is caught."""
    with session_factory.begin() as session:
        other = Source(name="Other", base_url="https://other.test")
        session.add(other)
        session.flush()
        session.add(
            SourceDocument(
                source_id=other.id,
                external_id="orphan",
                canonical_url="https://other.test/orphan",
                fetched_at=IN_RANGE,
                content_type="text/html",
                raw_content=b"",
            )
        )


def _load(
    service: MonitoringService,
    *,
    trigger: MonitoringTrigger = MonitoringTrigger.MANUAL,
    refetch_known: bool = False,
    published_from: date | None = None,
    published_to: date | None = None,
) -> tuple[int, DiscoveryResult, IngestionResult]:
    handle = service.start_source_run(
        SOURCE,
        trigger=trigger,
        refetch_known=refetch_known,
        published_from=published_from,
        published_to=published_to,
    )
    discovery = service.discover(handle)
    ingestion = service.ingest(handle, discovery)
    service.finish(handle)
    return handle.run_id, discovery, ingestion


def test_ingestion_stage_selects_fetches_skips_and_counts_as_before(
    session_factory: sessionmaker[Session],
) -> None:
    _offset_document_ids(session_factory)
    upstream = FakeUpstream()
    upstream.publish("a-known", "Уже сохранённая статья.", published_at=IN_RANGE)
    service = _service(session_factory, upstream, {})
    first_run, _, _ = _load(service)
    assert upstream.fetches == ["a-known"]
    upstream.fetches.clear()

    for external_id, text, published_at in [
        ("b-new", "Новая статья в окне дат.", IN_RANGE),
        ("c-media", MEDIA_ONLY, IN_RANGE),
        ("d-parse", "", IN_RANGE),
        ("e-transient", "не загрузится", IN_RANGE),
        ("f-permanent", "не загрузится", IN_RANGE),
        ("g-undated", "Статья без даты.", None),
        ("h-early", "Статья до окна дат.", BEFORE_RANGE),
        ("i-new", "Ещё одна статья в окне.", IN_RANGE),
    ]:
        upstream.publish(external_id, text, published_at=published_at)
    service = _service(
        session_factory,
        upstream,
        {
            "e-transient": TransientFetchError("HTTP 503"),
            "f-permanent": PermanentFetchError("HTTP 404"),
        },
    )

    run_id, discovery, ingestion = _load(
        service, published_from=date(2026, 9, 1), published_to=date(2026, 9, 30)
    )

    # Only the second run follows a completed load, so only it may stop at stored documents.
    assert upstream.discoveries_until_known == 1
    assert [ref.external_id for ref in discovery.references] == [
        "a-known",
        "b-new",
        "c-media",
        "d-parse",
        "e-transient",
        "f-permanent",
        "g-undated",
        "h-early",
        "i-new",
    ]
    assert discovery.known_external_ids == frozenset({"a-known"})
    # Known documents are not fetched again; every other one exactly once, in listing order.
    assert upstream.fetches == [
        "b-new",
        "c-media",
        "d-parse",
        "e-transient",
        "f-permanent",
        "g-undated",
        "h-early",
        "i-new",
    ]

    stored = _stored(session_factory)
    assert [external_id for external_id, _, _ in stored] == ["a-known", "b-new", "i-new"]
    assert ingestion.failed == 3
    with session_factory() as session:
        new_ids = session.scalars(
            select(ParsedArticleRecord.id)
            .join(SourceDocument, SourceDocument.id == ParsedArticleRecord.document_id)
            .where(SourceDocument.external_id.in_(["b-new", "i-new"]))
            .order_by(SourceDocument.id)
        ).all()
    assert ingestion.article_ids == tuple(new_ids)

    details = service.repository.get_run_details(run_id)
    assert details is not None
    run = details.run
    assert run.status is MonitoringRunStatus.COMPLETED_WITH_ERRORS
    assert (
        run.documents_discovered,
        run.documents_ingested,
        run.documents_skipped,
        run.documents_failed,
        run.error_count,
    ) == (9, 2, 4, 3, 3)
    ingestion_metrics = dict(run.stage_metrics[MonitoringStage.INGESTION.value])
    ingestion_metrics.pop("duration_ms")
    assert ingestion_metrics == {
        "attempted": 8,
        "ingested": 2,
        "skipped_no_text": 1,
        "failed_non_retryable": 2,
        "failed_retryable": 1,
        "skipped_unknown_published_date": 1,
        "skipped_out_of_published_range": 1,
    }
    discovery_metrics = dict(run.stage_metrics[MonitoringStage.DISCOVERY.value])
    discovery_metrics.pop("duration_ms")
    assert discovery_metrics == {"discovered": 9, "known": 1, "unchanged_skipped": 1}
    assert [
        (item.stage, item.entity_type, item.external_ref, item.failure_kind, item.error_type)
        for item in details.items
    ] == [
        (
            MonitoringStage.INGESTION,
            "source_reference",
            "https://ovd-info.test/d-parse",
            FailureKind.NON_RETRYABLE,
            "ParseError",
        ),
        (
            MonitoringStage.INGESTION,
            "source_reference",
            "https://ovd-info.test/e-transient",
            FailureKind.RETRYABLE,
            "TransientFetchError",
        ),
        (
            MonitoringStage.INGESTION,
            "source_reference",
            "https://ovd-info.test/f-permanent",
            FailureKind.NON_RETRYABLE,
            "PermanentFetchError",
        ),
    ]

    [state] = service.repository.list_source_states()
    assert (state.source_name, state.last_successful_run_id, state.last_discovered_count) == (
        SOURCE,
        run_id,
        9,
    )
    assert first_run < run_id


def test_backfill_refetches_known_documents_and_keeps_the_checkpoint(
    session_factory: sessionmaker[Session],
) -> None:
    upstream = FakeUpstream()
    upstream.publish("a-known", "Уже сохранённая статья.", published_at=IN_RANGE)
    service = _service(session_factory, upstream, {})
    first_run, _, _ = _load(service)
    upstream.publish("b-new", "Новая статья.", published_at=IN_RANGE)
    upstream.fetches.clear()

    run_id, _, ingestion = _load(service, trigger=MonitoringTrigger.BACKFILL, refetch_known=True)

    assert upstream.discoveries_until_known == 0
    assert upstream.fetches == ["a-known", "b-new"]
    assert len(ingestion.article_ids) == 2
    run = service.repository.get_run(run_id)
    assert run is not None
    assert (run.documents_discovered, run.documents_ingested, run.documents_skipped) == (2, 2, 0)
    [state] = service.repository.list_source_states()
    assert state.last_successful_run_id == first_run


def test_persistence_failure_stops_the_ingestion_stage(
    session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upstream = FakeUpstream()
    for external_id in ("a", "b", "c"):
        upstream.publish(external_id, f"Статья {external_id}.", published_at=IN_RANGE)
    service = _service(session_factory, upstream, {})
    original_save = SqlAlchemyIngestionPersistence.save

    def failing_save(
        self: SqlAlchemyIngestionPersistence, raw_document: RawDocument, article: ParsedArticle
    ) -> PersistenceResult:
        if raw_document.external_id == "b":
            raise PersistenceError("Failed to persist b")
        return original_save(self, raw_document, article)

    monkeypatch.setattr(SqlAlchemyIngestionPersistence, "save", failing_save)
    handle = service.start_source_run(SOURCE)
    discovery = service.discover(handle)

    with pytest.raises(PersistenceError, match="Failed to persist b"):
        service.ingest(handle, discovery)

    assert upstream.fetches == ["a", "b"]
    assert [external_id for external_id, _, _ in _stored(session_factory)] == ["a"]
    run = service.repository.get_run(handle.run_id)
    assert run is not None
    assert (run.documents_ingested, run.documents_failed, run.error_count) == (1, 0, 0)
    assert MonitoringStage.INGESTION.value not in run.stage_metrics
