"""Purging the articles without a criminal case, on PostgreSQL."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker
from support.db_fixtures import DatabaseSeeder
from support.monitoring_fixtures import SIDOROV, FakeUpstream, build_service

from db.orm_models import (
    JunkScreenHoldRecord,
    ParsedArticleRecord,
    PersecutionClassificationRecord,
    PersonRecord,
    ReviewRecordModel,
    SourceDocument,
)
from monitoring import cli
from monitoring.junk_holds import hold_again, mark_junk, reextract
from monitoring.junk_purge import (
    EXPIRED_CONTENT_TYPE,
    JunkPurge,
    JunkPurgeResult,
    since_from_env,
)
from monitoring.junk_screen import HELD, JunkScreenError


def _seed(session_factory: sessionmaker[Session]) -> dict[str, int]:
    with session_factory() as session:
        seed = DatabaseSeeder(session)
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

        fine, run = seed.article(
            source, external_id="fine", title="Штраф", text="Суд оштрафовал Анну Смирнову."
        )
        fine_event = seed.event(
            run, "Суд оштрафовал", event_type="fine", event_date=None, links=[(fined, "subject")]
        )

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


class Screen:
    """A stand-in screen: an article titled «Выставка» reads like a case."""

    name = "test-screen"
    cutoff = 0.5

    def __init__(self) -> None:
        self.judged: list[str] = []

    def scores(self, articles: Sequence[tuple[str, str]]) -> list[float]:
        self.judged += [title for title, _body in articles]
        return [0.9 if title == "Выставка" else 0.1 for title, _body in articles]


class BrokenScreen(Screen):
    def scores(self, articles: Sequence[tuple[str, str]]) -> list[float]:
        raise JunkScreenError("the model went away")


def _articles(session_factory: sessionmaker[Session]) -> set[int]:
    with session_factory() as session:
        return set(session.scalars(select(ParsedArticleRecord.id)).all())


def _holds(session_factory: sessionmaker[Session]) -> dict[int, str]:
    with session_factory() as session:
        return dict(
            session.execute(
                select(JunkScreenHoldRecord.article_id, JunkScreenHoldRecord.status)
            ).all()
        )


def test_the_screen_holds_what_reads_like_a_case_and_never_judges_it_again(
    session_factory: sessionmaker[Session],
) -> None:
    ids = _seed(session_factory)
    screen = Screen()

    first = JunkPurge(session_factory, screen=screen).run()
    again = Screen()
    second = JunkPurge(session_factory, screen=again).run()
    without = JunkPurge(session_factory).run()

    # The exhibition is held, the fine goes; the held one is judged once, and neither a
    # second purge nor one without the screen deletes it.
    assert (first.articles, first.held) == (1, 1)
    assert ids["junk"] in _articles(session_factory)
    assert ids["fine"] not in _articles(session_factory)
    assert sorted(screen.judged) == ["Выставка", "Штраф"]
    assert again.judged == [] and (second.articles, second.held) == (0, 0)
    assert without.articles == 0
    assert _holds(session_factory) == {ids["junk"]: HELD}
    assert JunkPurge(session_factory, screen=Screen()).count() == 0
    with session_factory() as session:
        hold = session.get_one(JunkScreenHoldRecord, ids["junk"])
    assert (hold.score, hold.cutoff, hold.screen) == (0.9, 0.5, "test-screen")
    assert "не доказательство" in hold.reason


def test_a_screen_that_fails_deletes_nothing(session_factory: sessionmaker[Session]) -> None:
    ids = _seed(session_factory)

    with pytest.raises(JunkScreenError):
        JunkPurge(session_factory, screen=BrokenScreen()).run()

    # The batch the screen could not judge stays whole.
    assert {ids["junk"], ids["fine"]} <= _articles(session_factory)
    assert _holds(session_factory) == {}


def test_what_a_person_calls_junk_goes_with_the_next_purge_unjudged(
    session_factory: sessionmaker[Session],
) -> None:
    ids = _seed(session_factory)
    JunkPurge(session_factory, screen=Screen()).run()
    with session_factory.begin() as session:
        assert mark_junk(session, ids["junk"])
        assert hold_again(session, ids["junk"])
        assert mark_junk(session, ids["junk"])
    screen = Screen()

    result = JunkPurge(session_factory, screen=screen).run()

    assert screen.judged == [] and result.articles == 1
    assert ids["junk"] not in _articles(session_factory)
    assert _holds(session_factory) == {}


def test_a_held_article_before_the_working_date_still_goes(
    session_factory: sessionmaker[Session],
) -> None:
    ids = _seed(session_factory)
    since = datetime(2026, 9, 20, tzinfo=UTC)
    with session_factory.begin() as session:
        session.execute(text("UPDATE parsed_articles SET published_at = :at"), {"at": since})
    JunkPurge(session_factory, since=since, screen=Screen()).run()
    with session_factory.begin() as session:
        session.execute(
            text("UPDATE parsed_articles SET published_at = :at WHERE id = :id"),
            {"at": since - timedelta(days=1), "id": ids["junk"]},
        )

    result = JunkPurge(session_factory, since=since, screen=Screen()).run()

    assert (result.articles, result.outdated) == (1, 1)
    assert ids["junk"] not in _articles(session_factory)
    assert _holds(session_factory) == {}


def test_extracting_a_held_article_again_releases_it_once_an_event_is_found(
    session_factory: sessionmaker[Session],
) -> None:
    ids = _seed(session_factory)
    JunkPurge(session_factory, screen=Screen()).run()

    unchanged = reextract(session_factory, ids["junk"])
    same_again = reextract(session_factory, ids["junk"])
    # The extraction fixed (here: the text says it plainly), the article goes back to work.
    with session_factory.begin() as session:
        session.execute(
            text("UPDATE parsed_articles SET text = :text WHERE id = :id"),
            {"text": "Суд арестовал Олега Орлова по делу о фейках.", "id": ids["junk"]},
        )
    fixed = reextract(session_factory, ids["junk"])

    assert not unchanged.released and "не нашло уголовного события" in unchanged.note
    assert not same_again.released and "повторять нечего" in same_again.note
    assert fixed.released and "arrest" in fixed.note
    assert _holds(session_factory) == {}
    # Released, it is no junk any more: a purge keeps it.
    JunkPurge(session_factory).run()
    assert ids["junk"] in _articles(session_factory)


def test_the_purge_command_with_a_broken_screen_stops_before_deleting(
    session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    ids = _seed(session_factory)
    monkeypatch.setenv("JUNK_SCREEN", "1")
    monkeypatch.setenv("JUNK_SCREEN_MODEL", str(tmp_path / "missing.json"))

    with pytest.raises(JunkScreenError):
        cli._purge_junk(session_factory)

    assert {ids["junk"], ids["fine"], ids["criminal"]} <= _articles(session_factory)
