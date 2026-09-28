"""Each work-cycle review kind has one focused page; legacy bookmarks redirect."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

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


def test_review_pages_are_focused_and_link_back_to_the_cycle(
    session_factory: sessionmaker[Session],
) -> None:
    with _client(session_factory) as client:
        pages = {
            path: client.get(path) for path in ("/ui/pairs", "/ui/roles", "/ui/politics-review")
        }

    assert {path: response.status_code for path, response in pages.items()} == dict.fromkeys(
        pages, 200
    )
    assert "Спорные совпадения людей" in pages["/ui/pairs"].text
    assert "Неясная роль в деле" in pages["/ui/roles"].text
    assert "Неясная политичность" in pages["/ui/politics-review"].text
    for page in pages.values():
        assert '<a href="/ui/cycle">Назад к циклу</a>' in page.text


def test_legacy_queue_management_and_disputes_addresses_redirect(
    session_factory: sessionmaker[Session],
) -> None:
    with _client(session_factory) as client:
        queue = client.get("/ui/queue", follow_redirects=False)
        disputes = client.get("/ui/disputes", follow_redirects=False)
        management = client.get("/ui/management?run_id=7", follow_redirects=False)

    assert (queue.status_code, queue.headers["location"]) == (303, "/ui/pairs")
    assert (disputes.status_code, disputes.headers["location"]) == (303, "/ui/pairs")
    assert (management.status_code, management.headers["location"]) == (
        303,
        "/ui/runs?run_id=7",
    )
