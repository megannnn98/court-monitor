"""Purging the articles without a criminal case, on PostgreSQL."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker
from support.monitoring_fixtures import SIDOROV, FakeUpstream, build_service
from support.research_db_fixtures import ResearchSeeder

from db.orm_models import (
    ParsedArticleRecord,
    PersecutionClassificationRecord,
    PersonRecord,
    ReviewRecordModel,
    SemanticDocumentRecord,
    SourceDocument,
)
from monitoring.junk_purge import (
    EXPIRED_CONTENT_TYPE,
    JunkPurge,
    JunkPurgeResult,
    since_from_env,
)


def _semantic(session: Session, entity_type: str, entity_id: int) -> None:
    session.add(
        SemanticDocumentRecord(
            entity_type=entity_type,
            entity_id=entity_id,
            representation_version=1,
            content_hash=f"{entity_type}-{entity_id}",
            text="текст",
        )
    )


def _seed(session_factory: sessionmaker[Session]) -> dict[str, int]:
    with session_factory() as session:
        seed = ResearchSeeder(session)
        source = seed.source("news", "https://news.example.test")
        kept = seed.person("Иван Иванов")
        only_in_junk = seed.person("Олег Орлов")
        fined = seed.person("Анна Смирнова")
        # Named in the criminal article, but no party to its event: a mention alone.
        witness = seed.person("Мария Свидетелева")

        criminal, run = seed.article(
            source,
            external_id="criminal",
            title="Арест",
            text="Суд арестовал Ивана Иванова. Мария Свидетелева дала показания.",
        )
        seed.mention(run, "Ивана Иванова", person_id=kept)
        seed.mention(run, "Мария Свидетелева", person_id=witness)
        seed.event(
            run, "Суд арестовал", event_type="arrest", event_date=None, links=[(kept, "subject")]
        )

        junk, run = seed.article(
            source,
            external_id="junk",
            title="Выставка",
            text="Иван Иванов, Мария Свидетелева и Олег Орлов открыли выставку.",
        )
        seed.mention(run, "Иван Иванов", person_id=kept)
        seed.mention(run, "Мария Свидетелева", person_id=witness)
        seed.mention(run, "Олег Орлов", person_id=only_in_junk)
        seed.classification(only_in_junk, "political", 0.9)
        _semantic(session, "person", only_in_junk)

        fine, run = seed.article(
            source, external_id="fine", title="Штраф", text="Суд оштрафовал Анну Смирнову."
        )
        fine_event = seed.event(
            run, "Суд оштрафовал", event_type="fine", event_date=None, links=[(fined, "subject")]
        )
        _semantic(session, "event", fine_event)

        # Its extraction failed: nothing to judge it by, it stays.
        unjudged, run = seed.article(
            source, external_id="unjudged", title="Сбой", text="Текст без разбора."
        )
        session.execute(
            text("UPDATE article_extraction_runs SET status = 'failed' WHERE id = :id"),
            {"id": run},
        )
        # A review whose decision is gone, and one of another kind.
        session.add(
            ReviewRecordModel(
                subject_type="person_resolution", subject_id=987654, decision="pending"
            )
        )
        session.add(
            ReviewRecordModel(
                subject_type="rosfinmonitoring_match", subject_id=1, decision="pending"
            )
        )
        session.commit()
        return {
            "kept": kept,
            "witness": witness,
            "only_in_junk": only_in_junk,
            "fined": fined,
            "criminal": criminal,
            "junk": junk,
            "fine": fine,
            "unjudged": unjudged,
            "fine_event": fine_event,
        }


def test_junk_articles_go_with_their_extraction_and_leave_a_tombstone(
    session_factory: sessionmaker[Session],
) -> None:
    ids = _seed(session_factory)
    progress: list[tuple[int, int]] = []

    def record(result: JunkPurgeResult) -> None:
        progress.append((result.articles, result.persons))

    purge = JunkPurge(session_factory, batch_size=1, on_progress=record)
    assert purge.count() == 2
    result = purge.run()

    assert (result.articles, result.persons, result.reviews) == (2, 2, 1)
    # One batch at a time, then the reviews.
    assert progress == [(1, 1), (2, 2), (2, 2)]
    with session_factory() as session:
        articles = set(session.scalars(select(ParsedArticleRecord.id)).all())
        assert articles == {ids["criminal"], ids["unjudged"]}
        # Every post is still known to discovery; only the junk lost its content.
        documents = {
            external_id: content
            for external_id, content in session.execute(
                select(SourceDocument.external_id, SourceDocument.raw_content)
            ).all()
        }
        assert documents == {
            "criminal": b"<html></html>",
            "junk": b"",
            "fine": b"",
            "unjudged": b"<html></html>",
        }
        # Named elsewhere, a person stays; named only in junk, it goes with what cascades.
        assert set(session.scalars(select(PersonRecord.id)).all()) == {ids["kept"], ids["witness"]}
        assert (
            session.scalar(select(func.count()).select_from(PersecutionClassificationRecord)) == 0
        )
        assert session.scalar(select(func.count()).select_from(SemanticDocumentRecord)) == 0
        assert list(session.scalars(select(ReviewRecordModel.subject_type)).all()) == [
            "rosfinmonitoring_match"
        ]


def test_a_second_purge_finds_nothing(session_factory: sessionmaker[Session]) -> None:
    _seed(session_factory)
    JunkPurge(session_factory).run()

    again = JunkPurge(session_factory).run()

    assert (again.articles, again.persons, again.reviews) == (0, 0, 0)


def test_a_purged_post_is_not_downloaded_again(session_factory: sessionmaker[Session]) -> None:
    ovd = FakeUpstream()
    ovd.publish("sidorov", SIDOROV)  # a detention: a criminal case
    ovd.publish("exhibition", "Пётр Петров открыл выставку современного искусства.")
    service = build_service(session_factory, {"ovd-info": ovd})
    service.run_source("ovd-info", with_derived=False)

    purged = JunkPurge(session_factory).run()
    fetches = list(ovd.fetches)
    again = service.run_source("ovd-info", with_derived=False)

    assert purged.articles == 1
    assert ovd.fetches == fetches
    assert (again.documents_skipped, again.documents_ingested) == (2, 0)


def test_an_article_before_the_working_date_goes_though_it_is_criminal(
    session_factory: sessionmaker[Session],
) -> None:
    ids = _seed(session_factory)
    since = datetime(2026, 9, 20, tzinfo=UTC)
    with session_factory.begin() as session:
        session.execute(text("UPDATE parsed_articles SET published_at = :at"), {"at": since})
        # The criminal article is a day too old; the unjudged one also, never extracted.
        session.execute(
            text("UPDATE parsed_articles SET published_at = :at WHERE id IN (:a, :b)"),
            {"at": since - timedelta(days=1), "a": ids["criminal"], "b": ids["unjudged"]},
        )

    purge = JunkPurge(session_factory, since=since)
    assert purge.count() == 4
    result = purge.run()

    assert (result.articles, result.outdated) == (4, 2)
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(ParsedArticleRecord)) == 0
        # Tombstones all; the ones before the working date are marked so.
        types = dict(
            session.execute(select(SourceDocument.external_id, SourceDocument.content_type)).all()
        )
        assert types["criminal"] == types["unjudged"] == EXPIRED_CONTENT_TYPE
        assert types["junk"] != EXPIRED_CONTENT_TYPE


def test_without_a_working_date_nothing_goes_for_its_age(
    session_factory: sessionmaker[Session],
) -> None:
    _seed(session_factory)
    with session_factory.begin() as session:
        session.execute(text("UPDATE parsed_articles SET published_at = '2004-01-01'"))

    assert JunkPurge(session_factory).run().outdated == 0


def test_the_working_date_comes_from_the_environment() -> None:
    assert since_from_env({"PIPELINE_SINCE": "2026-09-20"}) == datetime(2026, 9, 20, tzinfo=UTC)
    assert since_from_env({}) is None
