"""Re-extracting the stored corpus: which articles are stale, what a batch leaves behind."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Engine, func, select, update
from sqlalchemy.orm import Session, sessionmaker
from support.monitoring_fixtures import IVANOV, PETROV, SIDOROV

from cli_batches import current_extraction_versions, extract_articles
from ingestion.sources.sqlalchemy_persistence import SqlAlchemyIngestionPersistence
from ingestion.storage.models import ParsedArticleRecord
from monitor_core.model import ParsedArticle, RawDocument
from persons.read import event_evidence, person_stats
from persons.resolution.pending import SqlAlchemyPendingResolution
from persons.testing import add_person, link_event
from processing.extraction.pipeline import ExtractionVersions
from processing.extraction.reprocessing import plan_reprocessing, stale_articles
from processing.storage.models import (
    ArticleExtractionRunRecord,
    EntityMentionRecord,
    ExtractedEventRecord,
)

TEXTS = [SIDOROV, PETROV, IVANOV]


def _database_url(engine: Engine) -> str:
    return engine.url.render_as_string(hide_password=False)


def _save_articles(session_factory: sessionmaker[Session]) -> list[int]:
    persistence = SqlAlchemyIngestionPersistence(
        session_factory=session_factory,
        source_name="ОВД-Инфо",
        source_base_url="https://ovd.info",
    )
    for index, text in enumerate(TEXTS):
        external_id = f"reprocess-{index}"
        url = f"https://ovd.info/{external_id}"
        persistence.save(
            RawDocument(
                external_id=external_id,
                url=url,
                fetched_at=datetime(2026, 9, 15, tzinfo=UTC),
                content_type="text/html",
                content=text.encode(),
            ),
            ParsedArticle(
                external_id=external_id,
                url=url,
                title=external_id,
                published_at=datetime(2026, 9, 15, tzinfo=UTC),
                text=text,
            ),
        )
    with session_factory() as session:
        return sorted(session.scalars(select(ParsedArticleRecord.id)).all())


def _count(session_factory: sessionmaker[Session], record: type) -> int:
    with session_factory() as session:
        return session.scalar(select(func.count()).select_from(record)) or 0


def _age_runs(session_factory: sessionmaker[Session]) -> None:
    """Make every stored run one of an older version, as after a version bump."""
    with session_factory.begin() as session:
        session.execute(
            update(ArticleExtractionRunRecord).values(
                extractor_version="0.0.1", normalizer_version="0.0.1"
            )
        )


def test_every_article_without_a_current_run_is_stale_and_done_ones_are_not(
    session_factory: sessionmaker[Session], test_engine: Engine
) -> None:
    ids = _save_articles(session_factory)
    versions = current_extraction_versions(session_factory)
    assert [a.article_id for a in stale_articles(session_factory, versions)] == ids

    extract_articles(_database_url(test_engine), ids[:1])

    assert [a.article_id for a in stale_articles(session_factory, versions)] == ids[1:]


def test_dry_run_writes_nothing(
    session_factory: sessionmaker[Session], test_engine: Engine
) -> None:
    ids = _save_articles(session_factory)
    extract_articles(_database_url(test_engine), ids)
    _age_runs(session_factory)
    runs, mentions = (
        _count(session_factory, ArticleExtractionRunRecord),
        _count(session_factory, EntityMentionRecord),
    )

    plan = plan_reprocessing(session_factory, current_extraction_versions(session_factory), limit=2)

    assert plan.stale_total == len(ids)
    assert plan.selected == tuple(ids[:2])
    assert _count(session_factory, ArticleExtractionRunRecord) == runs
    assert _count(session_factory, EntityMentionRecord) == mentions


def test_a_batch_keeps_the_older_runs_and_is_idempotent(
    session_factory: sessionmaker[Session], test_engine: Engine
) -> None:
    url = _database_url(test_engine)
    ids = _save_articles(session_factory)
    extract_articles(url, ids)
    _age_runs(session_factory)
    old_mentions = _count(session_factory, EntityMentionRecord)
    versions = current_extraction_versions(session_factory)

    first = extract_articles(url, [a.article_id for a in stale_articles(session_factory, versions)])

    assert first.articles_processed == len(ids)
    assert _count(session_factory, ArticleExtractionRunRecord) == 2 * len(ids)
    assert _count(session_factory, EntityMentionRecord) >= old_mentions * 2
    assert stale_articles(session_factory, versions) == []
    again = extract_articles(url, ids)
    assert again.articles_processed == 0
    assert _count(session_factory, ArticleExtractionRunRecord) == 2 * len(ids)


def test_a_batch_can_be_limited_and_resumed_by_ids(
    session_factory: sessionmaker[Session], test_engine: Engine
) -> None:
    url = _database_url(test_engine)
    ids = _save_articles(session_factory)
    extract_articles(url, ids)
    _age_runs(session_factory)
    versions = current_extraction_versions(session_factory)

    only = plan_reprocessing(session_factory, versions, article_ids=[ids[1], 999_999])
    assert only.selected == (ids[1],)
    assert any("unknown or already extracted" in warning for warning in only.warnings)

    first = plan_reprocessing(session_factory, versions, limit=1)
    extract_articles(url, list(first.selected))
    rest = plan_reprocessing(session_factory, versions)
    assert rest.selected == tuple(ids[1:])


def _first_event_ids(session_factory: sessionmaker[Session], article_id: int) -> list[int]:
    """The first event of every run of the article, oldest run first."""
    with session_factory() as session:
        runs = session.scalars(
            select(ArticleExtractionRunRecord.id)
            .where(ArticleExtractionRunRecord.article_id == article_id)
            .order_by(ArticleExtractionRunRecord.id)
        ).all()
        return [
            session.scalars(
                select(ExtractedEventRecord.id)
                .where(ExtractedEventRecord.extraction_run_id == run_id)
                .order_by(ExtractedEventRecord.id)
            ).first()
            or 0
            for run_id in runs
        ]


def _person_with_two_runs(
    session_factory: sessionmaker[Session], test_engine: Engine
) -> tuple[int, list[int]]:
    url = _database_url(test_engine)
    article_id = _save_articles(session_factory)[0]
    extract_articles(url, [article_id])
    _age_runs(session_factory)
    extract_articles(url, [article_id])
    old_event, new_event = _first_event_ids(session_factory, article_id)
    assert old_event and new_event
    with session_factory.begin() as session:
        person_id = add_person(
            session, canonical_name="Иван Сидоров", normalized_name="иван сидоров", matching_key="k"
        )
    return person_id, [old_event, new_event]


def test_one_publication_is_not_counted_twice_after_a_reextraction(
    session_factory: sessionmaker[Session], test_engine: Engine
) -> None:
    person_id, (old_event, new_event) = _person_with_two_runs(session_factory, test_engine)
    with session_factory.begin() as session:
        link_event(session, person_id, old_event, role="defendant")
        link_event(session, person_id, new_event, role="defendant")

    with session_factory() as session:
        assert person_stats(session, [person_id])[person_id].event_count == 1
        events = event_evidence(session, person_id)
        assert [event.id for event in events] == [new_event]


def test_a_link_to_the_older_run_alone_stays_visible(
    session_factory: sessionmaker[Session], test_engine: Engine
) -> None:
    person_id, (old_event, _) = _person_with_two_runs(session_factory, test_engine)
    with session_factory.begin() as session:
        link_event(session, person_id, old_event, role="defendant")

    with session_factory() as session:
        assert person_stats(session, [person_id])[person_id].event_count == 1
        assert [event.id for event in event_evidence(session, person_id)] == [old_event]


def _run_with_person(
    session_factory: sessionmaker[Session],
    article_id: int,
    *,
    content_hash: str,
    extractor_version: str,
) -> int:
    """A succeeded run of the article with one unlinked person mention; the run id."""
    at = datetime(2026, 9, 16, tzinfo=UTC)
    with session_factory.begin() as session:
        run = ArticleExtractionRunRecord(
            article_id=article_id,
            article_content_hash=content_hash,
            extractor_name="rule-based",
            extractor_version=extractor_version,
            normalizer_version="1",
            status="succeeded",
            started_at=at,
            finished_at=at,
        )
        session.add(run)
        session.flush()
        session.add(
            EntityMentionRecord(
                extraction_run_id=run.id,
                entity_type="person",
                surface_text="Иван Иванов",
                normalized_text="иван иванов",
                start_offset=0,
                end_offset=11,
                confidence=1.0,
                normalized_data={},
                extractor_name="rule-based",
                extractor_version=extractor_version,
                normalizer_version="1",
            )
        )
        return run.id


def test_a_reextraction_of_the_same_text_is_not_resolved_by_monitoring(
    session_factory: sessionmaker[Session],
) -> None:
    new, same_text, changed_text = _save_articles(session_factory)
    versions = ExtractionVersions("rule-based", "2", "1")
    new_run = _run_with_person(session_factory, new, content_hash="a", extractor_version="2")
    _run_with_person(session_factory, same_text, content_hash="b", extractor_version="1")
    _run_with_person(session_factory, same_text, content_hash="b", extractor_version="2")
    _run_with_person(session_factory, changed_text, content_hash="c", extractor_version="1")
    refetched = _run_with_person(
        session_factory, changed_text, content_hash="d", extractor_version="2"
    )

    pending = SqlAlchemyPendingResolution(session_factory).extraction_runs_pending_resolution(
        source_base_url="https://ovd.info", versions=versions
    )

    assert pending == [new_run, refetched]
