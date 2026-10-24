"""Re-extracting the stored corpus: which articles are stale, what a batch leaves behind."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast

import pytest
from sqlalchemy import Engine, func, select, update
from sqlalchemy.orm import Session, sessionmaker
from support.monitoring_fixtures import IVANOV, PETROV, SIDOROV

import cli.extraction as cli_extraction
from cli.app import build_parser
from cli.context import CliContext
from cli_batches import current_extraction_versions, extract_articles
from ingestion import read
from ingestion.sources.sqlalchemy_persistence import SqlAlchemyIngestionPersistence
from ingestion.storage.models import ParsedArticleRecord
from monitor_core.model import ParsedArticle, RawDocument
from persons.read import event_evidence, latest_events, person_stats
from persons.resolution.pending import SqlAlchemyPendingResolution
from persons.testing import add_person, link_event
from processing.extraction.models import BatchExtractionResult
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
    extractor_name: str = "rule-based",
) -> int:
    """A succeeded run of the article with one unlinked person mention; the run id."""
    at = datetime(2026, 9, 16, tzinfo=UTC)
    with session_factory.begin() as session:
        run = ArticleExtractionRunRecord(
            article_id=article_id,
            article_content_hash=content_hash,
            extractor_name=extractor_name,
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
                extractor_name=extractor_name,
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


def test_only_a_reextraction_of_unchanged_text_by_the_same_extractor_is_held_back(
    session_factory: sessionmaker[Session],
) -> None:
    text_back, other_extractor, unchanged = _save_articles(session_factory)
    versions = ExtractionVersions("rule-based", "2", "1")
    _run_with_person(session_factory, text_back, content_hash="a", extractor_version="1")
    _run_with_person(session_factory, text_back, content_hash="b", extractor_version="1")
    # The text is back to the one of an older run, but the run before it read another text.
    changed_back = _run_with_person(
        session_factory, text_back, content_hash="a", extractor_version="2"
    )
    _run_with_person(
        session_factory,
        other_extractor,
        content_hash="x",
        extractor_version="1",
        extractor_name="other",
    )
    first_of_extractor = _run_with_person(
        session_factory, other_extractor, content_hash="x", extractor_version="2"
    )
    _run_with_person(session_factory, unchanged, content_hash="u", extractor_version="1")
    _run_with_person(session_factory, unchanged, content_hash="u", extractor_version="2")

    pending = SqlAlchemyPendingResolution(session_factory).extraction_runs_pending_resolution(
        source_base_url="https://ovd.info", versions=versions
    )

    assert pending == [changed_back, first_of_extractor]


def test_an_article_whose_run_of_the_current_versions_failed_is_still_stale(
    session_factory: sessionmaker[Session], test_engine: Engine
) -> None:
    ids = _save_articles(session_factory)
    extract_articles(_database_url(test_engine), ids)
    versions = current_extraction_versions(session_factory)
    with session_factory.begin() as session:
        for run in session.scalars(
            select(ArticleExtractionRunRecord).where(
                ArticleExtractionRunRecord.article_id.in_(ids[:2])
            )
        ):
            run.status = "failed"

    assert [a.article_id for a in stale_articles(session_factory, versions)] == ids[:2]
    # A persistent failure holds the front of every batch; --article-ids is the way past it.
    assert plan_reprocessing(session_factory, versions, limit=1).selected == (ids[0],)
    assert plan_reprocessing(session_factory, versions, article_ids=[ids[1]], limit=1).selected == (
        ids[1],
    )


def _context(session_factory: sessionmaker[Session], test_engine: Engine) -> CliContext:
    return cast(
        CliContext,
        SimpleNamespace(
            session_factory=session_factory,
            engine=test_engine,
            settings=SimpleNamespace(database_url=_database_url(test_engine)),
        ),
    )


def test_the_dry_run_command_reports_and_writes_nothing(
    session_factory: sessionmaker[Session],
    test_engine: Engine,
    capsys: pytest.CaptureFixture[str],
) -> None:
    ids = _save_articles(session_factory)
    args = build_parser().parse_args(["extract-entities", "--stale", "--dry-run", "--limit", "2"])

    with pytest.raises(SystemExit) as stopped:
        cli_extraction.run_extract_entities(args, _context(session_factory, test_engine))

    assert stopped.value.code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["dry_run"] is True
    assert (report["stale_articles_total"], report["selected_count"]) == (len(ids), 2)
    assert _count(session_factory, ArticleExtractionRunRecord) == 0


def test_a_failed_article_ends_the_command_with_an_error_after_the_report(
    session_factory: sessionmaker[Session],
    test_engine: Engine,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _save_articles(session_factory)
    failed = BatchExtractionResult(articles_failed=1, failures=["article_id=1: boom"])
    monkeypatch.setattr(cli_extraction, "run_chunks", lambda *a, **k: [failed])
    args = build_parser().parse_args(["extract-entities", "--stale", "--limit", "1"])

    with pytest.raises(SystemExit) as stopped:
        cli_extraction.run_extract_entities(args, _context(session_factory, test_engine))

    assert stopped.value.code == 1
    assert "stale articles left" in capsys.readouterr().out


def test_the_latest_event_of_a_person_linked_to_the_older_run_alone_is_visible(
    session_factory: sessionmaker[Session], test_engine: Engine
) -> None:
    person_id, (old_event, _) = _person_with_two_runs(session_factory, test_engine)
    with session_factory.begin() as session:
        link_event(session, person_id, old_event, role="defendant")

    with session_factory() as session:
        assert person_id in latest_events(session, [person_id])


@pytest.mark.parametrize("limit", [-1, -100])
def test_a_negative_limit_is_refused_before_anything_is_selected(
    session_factory: sessionmaker[Session], limit: int
) -> None:
    _save_articles(session_factory)
    versions = current_extraction_versions(session_factory)

    with pytest.raises(ValueError, match="limit"):
        plan_reprocessing(session_factory, versions, limit=limit)


def test_a_limit_of_zero_selects_nothing_and_one_selects_one(
    session_factory: sessionmaker[Session],
) -> None:
    ids = _save_articles(session_factory)
    versions = current_extraction_versions(session_factory)

    assert plan_reprocessing(session_factory, versions, limit=0).selected == ()
    assert plan_reprocessing(session_factory, versions, limit=1).selected == (ids[0],)


def test_the_command_refuses_a_negative_limit(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as stopped:
        build_parser().parse_args(["extract-entities", "--stale", "--limit", "-1"])

    assert stopped.value.code == 2
    assert "--limit" in capsys.readouterr().err


def test_an_article_deleted_between_the_two_reads_is_left_out(
    session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    ids = _save_articles(session_factory)
    versions = current_extraction_versions(session_factory)
    original = read.text_lengths
    monkeypatch.setattr(
        read,
        "text_lengths",
        lambda session, wanted: {
            key: value for key, value in original(session, wanted).items() if key != ids[1]
        },
    )

    assert [a.article_id for a in stale_articles(session_factory, versions)] == [ids[0], ids[2]]


def test_stale_articles_asked_by_id_come_in_id_order_without_unknown_or_repeated_ids(
    session_factory: sessionmaker[Session],
) -> None:
    ids = _save_articles(session_factory)
    versions = current_extraction_versions(session_factory)

    asked = [ids[2], 999_999, ids[0], ids[0]]

    assert [a.article_id for a in stale_articles(session_factory, versions, article_ids=asked)] == [
        ids[0],
        ids[2],
    ]
    assert stale_articles(session_factory, versions, article_ids=[]) == []


def test_zero_workers_is_refused_as_before(
    session_factory: sessionmaker[Session], test_engine: Engine
) -> None:
    _save_articles(session_factory)
    args = build_parser().parse_args(["extract-entities", "--stale", "--workers", "0"])

    with pytest.raises(ValueError, match="workers"):
        cli_extraction.run_extract_entities(args, _context(session_factory, test_engine))


def test_the_latest_event_follows_the_newest_run_with_a_link_of_the_person(
    session_factory: sessionmaker[Session], test_engine: Engine
) -> None:
    person_id, (old_event, new_event) = _person_with_two_runs(session_factory, test_engine)
    old_day, new_day = datetime(2026, 9, 10, tzinfo=UTC), datetime(2026, 9, 1, tzinfo=UTC)
    with session_factory.begin() as session:
        other_id = add_person(
            session, canonical_name="Пётр Петров", normalized_name="пётр петров", matching_key="o"
        )
        for event_id, day in ((old_event, old_day), (new_event, new_day)):
            session.execute(
                update(ExtractedEventRecord)
                .where(ExtractedEventRecord.id == event_id)
                .values(event_date=day)
            )
        link_event(session, person_id, old_event, role="defendant")
        link_event(session, person_id, new_event, role="defendant")
        link_event(session, other_id, old_event, role="defendant")

    with session_factory() as session:
        latest = latest_events(session, [person_id, other_id])

    # Without the filter the older run's later day would win for the first person.
    assert latest[person_id].event_date == new_day
    assert latest[other_id].event_date == old_day
