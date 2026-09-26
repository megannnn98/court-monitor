"""«Безымянные»: the unnamed figurants, their candidates from the list, a person's word."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker
from support.research_db_fixtures import ResearchSeeder

from db.orm_models import (
    RosfinmonitoringEntryRecord,
    RosfinmonitoringSnapshotRecord,
    UnnamedFigurantRecord,
)
from operator_console import OperationRegistry
from web.app import app
from web.dependencies import get_db, get_operation_registry

QUOTE = "В Тюмени задержан 17-летний житель города по делу о теракте <b>на железной дороге</b>."


@contextmanager
def _client(session_factory: sessionmaker[Session]) -> Iterator[TestClient]:
    registry = OperationRegistry(session_factory, executor=lambda _work: None)

    def override_get_db() -> Iterator[Session]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_operation_registry] = lambda: registry
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_operation_registry, None)


def _seed(session_factory: sessionmaker[Session]) -> None:
    with session_factory() as session:
        seed = ResearchSeeder(session)
        source = seed.source("news", "https://news.example.test")
        article, _ = seed.article(
            source,
            external_id="tyumen",
            title="Тюмень",
            text=QUOTE,
            published_at=datetime(2024, 11, 25, tzinfo=UTC),
        )
        session.add(
            UnnamedFigurantRecord(
                key="k" * 64,
                article_id=article,
                start_offset=0,
                end_offset=len(QUOTE),
                quote=QUOTE,
                age=17,
                gender="male",
                place="Тюмень",
                initial=None,
                articles=["205"],
                event_type="detention",
                explanation="задержан по делу о теракте",
                published_at=datetime(2024, 11, 25, tzinfo=UTC),
            )
        )
        snapshot = RosfinmonitoringSnapshotRecord(
            snapshot_date=datetime(2024, 12, 1, tzinfo=UTC),
            source_url="https://fedsfm.test",
            content_hash="a",
            entry_count=1,
            fetched_at=datetime(2024, 12, 1, tzinfo=UTC),
        )
        session.add(snapshot)
        session.flush()
        session.add(
            RosfinmonitoringEntryRecord(
                snapshot_id=snapshot.id,
                full_name="ПУРТОВ ЕГОР ВЛАДИМИРОВИЧ",
                normalized_name="пуртов егор владимирович",
                matching_key="пуртовегорвладимирович",
                birth_date=datetime(2007, 2, 17, tzinfo=UTC),
                birth_place="Г. ТЮМЕНЬ ТЮМЕНСКОЙ ОБЛАСТИ",
            )
        )
        session.commit()


def test_nobody_yet_says_where_they_come_from(session_factory: sessionmaker[Session]) -> None:
    with _client(session_factory) as client:
        page = client.get("/ui/unnamed").text

    assert "<title>Безымянные</title>" in page
    assert "Безымянных фигурантов пока нет: их находит шаг 6" in page
    assert '<span>Безымянные</span><span class="nav-count">' not in page
    assert 'href="/ui/unnamed" aria-current="page"' in page


def test_a_card_shows_the_text_what_it_tells_and_the_candidates(
    session_factory: sessionmaker[Session],
) -> None:
    _seed(session_factory)

    with _client(session_factory) as client:
        page = client.get("/ui/unnamed").text
        queue = client.get("/ui/queue").text

    assert "17 лет · мужчина · Тюмень · задержание · ст. 205" in page
    # Scraped text is escaped; the sentence leads to its publication.
    assert "&lt;b&gt;на железной дороге&lt;/b&gt;" in page and "<b>на железной" not in page
    assert '<a href="/ui/articles/' in page and "?start=0&amp;end=" in page
    assert "ПУРТОВ ЕГОР ВЛАДИМИРОВИЧ" in page and "17.02.2007" in page
    assert "17 лет на 25.11.2024; мужчина; родился: Г. ТЮМЕНЬ ТЮМЕНСКОЙ ОБЛАСТИ" in page
    assert "в перечне с 01.12.2024 или раньше" in page
    assert "Не разобраны (1)" in page and '<span class="badge pending">не разобран</span>' in page
    # The queue counts it, and leads here.
    assert '<a class="chip" href="/ui/unnamed">Безымянные: 1</a>' in queue


def test_a_person_s_word_identifies_and_closes(session_factory: sessionmaker[Session]) -> None:
    _seed(session_factory)
    candidate = "пуртов егор владимирович|2007-02-17"

    with _client(session_factory) as client:
        same = client.post(
            "/ui/unnamed/decide",
            data={
                "figurant": "k" * 64,
                "candidate": candidate,
                "decision": "same",
                "back": "status=open&page=1",
            },
            follow_redirects=False,
        )
        found = client.get("/ui/unnamed", params={"status": "found"}).text
        still_open = client.get("/ui/unnamed").text
        incomplete = client.post(
            "/ui/unnamed/decide", data={"figurant": "k" * 64, "decision": "same"}
        )
        unknown = client.post("/ui/unnamed/decide", data={"figurant": "x", "decision": "none"})

    assert same.status_code == 303
    assert same.headers["location"] == f"/ui/unnamed?status=open&page=1#u-{'k' * 64}"
    assert "Опознаны (1)" in found and "опознан: ПУРТОВ ЕГОР ВЛАДИМИРОВИЧ" in found
    assert '<span class="badge succeeded">это он</span>' in found
    assert "В этом разделе никого." in still_open
    assert incomplete.status_code == 400 and unknown.status_code == 400
    with session_factory() as session:
        assert session.execute(text("SELECT candidate, decision FROM unnamed_decisions")).all() == [
            (candidate, "same")
        ]


def test_nobody_on_the_list_closes_the_case(session_factory: sessionmaker[Session]) -> None:
    _seed(session_factory)

    with _client(session_factory) as client:
        client.post(
            "/ui/unnamed/decide", data={"figurant": "k" * 64, "candidate": "", "decision": "none"}
        )
        closed = client.get("/ui/unnamed", params={"status": "none"}).text
        queue = client.get("/ui/queue").text

    assert (
        "Никого в перечне (1)" in closed and '<span class="badge">никого в перечне</span>' in closed
    )
    assert '<a class="chip" href="/ui/unnamed">Безымянные: 0</a>' in queue
