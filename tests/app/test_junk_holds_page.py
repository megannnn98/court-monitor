"""«Отсев: на проверке»: the articles the junk screen held back, and a person's word."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker
from support.db_fixtures import DatabaseSeeder

from db.orm_models import JunkScreenHoldRecord
from monitoring.junk_screen import reason
from web.app import app
from web.dependencies import get_db


@contextmanager
def _client(session_factory: sessionmaker[Session]) -> Iterator[TestClient]:
    def override_get_db() -> Iterator[Session]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)


def _held(session_factory: sessionmaker[Session], body: str) -> int:
    with session_factory() as session:
        seed = DatabaseSeeder(session)
        source = seed.source("news", "https://news.example.test")
        article, _run = seed.article(
            source,
            external_id="digest",
            title="Главные новости <b>дня</b>",
            text=body,
            published_at=datetime(2026, 9, 25, tzinfo=UTC),
        )
        session.add(
            JunkScreenHoldRecord(
                article_id=article,
                status="held",
                score=0.81,
                cutoff=0.52,
                screen="junk-screen-v1:e5",
                reason=reason(0.81, 0.52),
            )
        )
        session.commit()
        return article


def _status(session_factory: sessionmaker[Session], article: int) -> str | None:
    with session_factory() as session:
        return session.scalar(
            text("SELECT status FROM junk_screen_holds WHERE article_id = :id"), {"id": article}
        )


def test_the_held_articles_are_listed_with_why_and_the_queue_counts_them(
    session_factory: sessionmaker[Session],
) -> None:
    article = _held(session_factory, "Сегодня в городе прошла выставка.")

    with _client(session_factory) as client:
        page = client.get("/ui/junk-holds").text
        cycle = client.get("/ui/cycle").text

    assert "<title>Отсев: на проверке</title>" in page
    assert f'href="/ui/articles/{article}">Главные новости &lt;b&gt;дня&lt;/b&gt;</a>' in page
    assert "оценка 0.81 (порог 0.52)" in page and "не доказательство уголовного дела" in page
    assert "На проверке (1)" in page and "Извлечь заново" in page
    assert 'data-primary-task="junk_holds"' in cycle
    assert "Публикации на проверке: 1" in cycle


def test_junk_and_back_again(session_factory: sessionmaker[Session]) -> None:
    article = _held(session_factory, "Сегодня в городе прошла выставка.")

    with _client(session_factory) as client:
        junk = client.post(
            "/ui/junk-holds/junk",
            data={"article": article, "back": "status=held&page=1"},
            follow_redirects=False,
        )
        marked = _status(session_factory, article)
        listed = client.get("/ui/junk-holds", params={"status": "junk"}).text
        back = client.post("/ui/junk-holds/hold", data={"article": article}, follow_redirects=False)
        twice = client.post("/ui/junk-holds/hold", data={"article": article})
        nothing = client.post("/ui/junk-holds/junk", data={"article": "999999"})

    assert junk.status_code == 303
    assert junk.headers["location"] == f"/ui/junk-holds?status=held&page=1#a-{article}"
    assert marked == "junk" and "удалится при следующей очистке" in listed
    assert back.status_code == 303 and _status(session_factory, article) == "held"
    assert twice.status_code == 404 and nothing.status_code == 404


def test_extracting_again_releases_an_article_whose_event_is_found(
    session_factory: sessionmaker[Session],
) -> None:
    article = _held(session_factory, "Суд арестовал Олега Орлова по делу о фейках.")

    with _client(session_factory) as client:
        released = client.post(
            "/ui/junk-holds/reextract", data={"article": article}, follow_redirects=False
        )
        page = client.get(released.headers["location"]).text
        gone = client.post("/ui/junk-holds/reextract", data={"article": article})

    assert released.status_code == 303 and f"released={article}" in released.headers["location"]
    assert _status(session_factory, article) is None
    assert "она возвращена в работу" in page
    assert gone.status_code == 404
