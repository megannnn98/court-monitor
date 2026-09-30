"""The published list is refreshed by a button, not by a file.

The operator's path is one click on «Справочники»: the route starts the operation that
already exists and hands the operator its card. There is no file input any more, and no
second downloader — the published list is downloaded in exactly one place, by the stage
that has always downloaded it.

These tests use a real `OperationRegistry` with a do-nothing executor, so nothing here
reaches fedsfm.ru and nothing here fakes the queue either: who may start, and what a
conflict looks like, are the registry's own answers.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from operator_console import OperationRegistry
from web.app import app
from web.dependencies import get_db, get_operation_registry


def _executor(_work: object) -> None:
    """Never runs the work, so a started run stays in flight — which is what makes a
    second start a real conflict rather than a staged one."""


@contextmanager
def _client(session_factory: sessionmaker[Session]) -> Iterator[TestClient]:
    registry = OperationRegistry(session_factory, executor=_executor)

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


def registry_runs(session: Session) -> list:
    from db.orm_models import OperatorOperationRunRecord

    return list(
        session.scalars(select(OperatorOperationRunRecord).order_by(OperatorOperationRunRecord.id))
    )


def test_the_page_offers_the_button_and_no_file(session_factory: sessionmaker[Session]) -> None:
    with _client(session_factory) as client:
        page = client.get("/ui/airtable").text

    assert "Обновить перечень и сверить с РФМ" in page
    assert 'action="/ui/airtable/rosfin"' in page
    # The file is gone, and nothing of it is left behind: no hidden input, no button that
    # only works if you know the endpoint, no prose describing a fallback that is not there.
    assert "official-file" not in page
    assert "import-button" not in page
    assert "Загрузить перечень" not in page
    assert "загрузите файл" not in page


def test_the_page_says_what_the_button_does(session_factory: sessionmaker[Session]) -> None:
    """A button that says «обновить» and does not say what happens next is the thing this
    change is about: the operator has to know that a snapshot is made only on a real
    change, and that the check runs again — otherwise «Кандидаты» look empty and nobody
    knows why."""
    with _client(session_factory) as client:
        page = client.get("/ui/airtable").text

    assert "fedsfm.ru" in page
    assert "только если перечень изменился" in " ".join(page.split())
    assert "пересверяет людей с перечнем" in " ".join(page.split())


def test_the_page_still_names_the_official_snapshot(
    session_factory: sessionmaker[Session],
) -> None:
    with _client(session_factory) as client:
        page = client.get("/ui/airtable").text

    assert "Официальный перечень РФМ (fedsfm.ru)" in page


def test_the_button_starts_the_existing_rosfin_operation(
    session_factory: sessionmaker[Session],
) -> None:
    """Not a download in an HTTP request: the operation that already exists, by the same
    name the CLI and step 5 use, started through the same registry as every other step."""
    with _client(session_factory) as client:
        answer = client.post("/ui/airtable/rosfin", follow_redirects=False)

    assert answer.status_code == 303
    assert answer.headers["location"].startswith("/ui/runs?run_id=")

    with session_factory() as session:
        runs = registry_runs(session)
    assert len(runs) == 1
    assert runs[0].operation_name == "monitor"
    # The parameters are stored whole, the mode among them: what runs is
    # `check-entities-rosfin`, chosen by the operation itself from the mode.
    assert runs[0].parameters["mode"] == "rosfin"


def test_a_run_in_flight_starts_no_second(
    session_factory: sessionmaker[Session],
) -> None:
    """The one that matters: a second run beside a first is what would fight over the
    snapshot. The first run never finishes here, so the second would have to be refused —
    and it is, by the registry itself, before a run row is written."""
    with _client(session_factory) as client:
        first = client.post("/ui/airtable/rosfin", follow_redirects=False)
        second = client.post("/ui/airtable/rosfin", follow_redirects=False)

    assert first.status_code == 303
    assert second.status_code == 409
    with session_factory() as session:
        started = [row for row in registry_runs(session) if row.parameters["mode"] == "rosfin"]
    assert len(started) == 1


def test_the_route_is_registered_and_the_file_route_is_not() -> None:
    """The exact route set, so neither a return nor a leftover can pass unnoticed.

    FastAPI keeps an included router as one entry with its routes inside, so they have to
    be walked into — the same way the architecture guard does it.
    """

    def walk(routes: object) -> Iterator[object]:
        for route in routes:  # type: ignore[attr-defined]
            original = getattr(route, "original_router", None)
            if original is not None:
                yield from walk(original.routes)
            elif isinstance(route, APIRoute):
                yield route

    paths = {
        f"{method} {route.path}" for route in walk(app.routes) for method in route.methods or ()
    }

    assert "POST /ui/airtable/rosfin" in paths
    assert "POST /api/admin/rosfinmonitoring/import" not in paths


def test_the_cli_command_is_still_the_same_work() -> None:
    """The page may not offer a file, but the answer to «как это делается без
    интерфейса» is still the command that has always done it."""
    from pathlib import Path

    source = (Path(__file__).resolve().parents[2] / "src" / "web" / "ui" / "airtable.py").read_text(
        encoding="utf-8"
    )

    assert "check-entities-rosfin" in source
