"""The queue for the customer's channel (`/ui/channel`)."""

from __future__ import annotations

import html
import re
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker
from support.research_db_fixtures import ResearchSeeder

import api
from api import app, get_db, get_published_name_keys
from channel_feed.published import name_key

NEWS_TIME = datetime.now(UTC) - timedelta(days=2)


@contextmanager
def _client(
    session_factory: sessionmaker[Session], published: frozenset[str]
) -> Iterator[TestClient]:
    def override_get_db() -> Iterator[Session]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_published_name_keys] = lambda: published
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_published_name_keys, None)


def _seed(seed: ResearchSeeder, snapshot_id: int, name: str, rf_status: str) -> int:
    person_id = seed.person(name)
    seed.classification(person_id, "political", 0.9, reasons=["Политическая статья: УК РФ ст. 275"])
    seed.match(person_id, snapshot_id, rf_status, 0.8)
    source_id = seed.source(f"source-{person_id}", f"https://news-{person_id}.example.test")
    _, run_id = seed.article(
        source_id,
        external_id=f"news-{person_id}",
        title="Новость",
        text=f"Суд приговорил {name}.",
        published_at=NEWS_TIME,
    )
    seed.event(
        run_id,
        f"Суд приговорил {name}",
        event_type="sentence",
        event_date=NEWS_TIME,
        links=[(person_id, "subject")],
    )
    return person_id


def _drafts(page: str) -> list[str]:
    return [
        html.unescape(draft)
        for draft in re.findall(r"<textarea[^>]*>(.*?)</textarea>", page, re.DOTALL)
    ]


def test_the_queue_leaves_out_the_published_and_keeps_people_on_the_list(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        seed = ResearchSeeder(session)
        snapshot_id = seed.snapshot()
        _seed(seed, snapshot_id, "Сергей Ярош", "not_matched")
        _seed(seed, snapshot_id, "Дмитрий Коротков", "matched")
        _seed(seed, snapshot_id, "Андрей Смирнов", "not_matched")
        session.commit()
    published = frozenset({name_key("Смирнов Андрей Петрович") or ""})

    with _client(session_factory, published) as client:
        response = client.get("/ui/channel")

    assert response.status_code == 200
    drafts = _drafts(response.text)
    # Names are written surname first, as the customer's table does.
    assert [draft.split("\n", 1)[0] for draft in drafts] == ["Ярош Сергей", "Коротков Дмитрий"]
    assert "Сергей Ярош" not in response.text
    assert "Внимание: в перечне Росфинмониторинга." in drafts[1]
    assert "Вынесен приговор, статьи: ст. 275 УК РФ." in drafts[0]
    assert "В очереди: 2 (уже опубликовано в канале: 1)" in response.text
    assert "Не удалось прочитать канал" not in response.text


def test_an_unreadable_channel_leaves_nothing_out_and_says_so(
    session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    def unreachable(ttl_seconds: float = 3600.0) -> frozenset[str]:
        raise httpx.ConnectError("no network")

    monkeypatch.setattr(api, "load_published_keys", unreachable)

    assert get_published_name_keys() == frozenset()

    with session_factory() as session:
        seed = ResearchSeeder(session)
        snapshot_id = seed.snapshot()
        _seed(seed, snapshot_id, "Сергей Ярош", "not_matched")
        session.commit()

    with _client(session_factory, frozenset()) as client:
        response = client.get("/ui/channel")

    assert "Не удалось прочитать канал" in response.text
    assert len(_drafts(response.text)) == 1
