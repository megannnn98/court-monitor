"""The build stamp the console runs on («О системе» reads it from `/api/v1/about`).

Local fixtures only — no database, no network, no embeddings.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from web.app import app
from web.build_info import UNKNOWN, build_info
from web.dependencies import get_db


@contextmanager
def _client(session_factory: sessionmaker[Session]) -> Iterator[TestClient]:
    def override() -> Iterator[Session]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)


# The build stamp


def test_a_stamped_build_says_which_commit_it_is() -> None:
    info = build_info({"BUILD_COMMIT": "04499dfabc1234def", "BUILD_TIME": "2026-09-29T18:00:00Z"})

    assert info.commit == "04499dfabc12", "a short hash is enough and fits the page"
    assert info.built_at == "2026-09-29T18:00:00Z"
    assert info.version == UNKNOWN, "no tag told: no release to name"


def test_a_working_copy_says_it_does_not_know() -> None:
    info = build_info({})

    assert info.commit == UNKNOWN
    assert info.built_at == UNKNOWN


def test_the_stamp_degrades_instead_of_guessing() -> None:
    assert build_info({"BUILD_COMMIT": "   "}).commit == UNKNOWN
    assert build_info({"BUILD_COMMIT": "04499df"}).commit == "04499df"


def test_a_stamped_build_says_which_tag_it_stands_on() -> None:
    """The operator asks «which version is this»: the release tag answers, the hash does not."""
    info = build_info({"BUILD_COMMIT": "495d9e9", "BUILD_TAG": " 0.36.0-1-g495d9e9 "})

    assert info.tag == "0.36.0-1-g495d9e9"
    assert build_info({"BUILD_COMMIT": "495d9e9"}).tag == UNKNOWN
