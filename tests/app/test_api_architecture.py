"""The shape of the HTTP layer after splitting `api.py` into the `web` package.

`tests/fixtures/api_routes.json` is the route list of `api.py` before the split: every
route there must still exist, answered by exactly one handler.
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from collections import Counter
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import cast

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from starlette.routing import BaseRoute

from api import app

ROOT = Path(__file__).parents[2]
SRC = ROOT / "src"
ROUTES_BEFORE_SPLIT = json.loads((ROOT / "tests/fixtures/api_routes.json").read_text())
# Routes added after that snapshot was taken. The fixture stays a record of what
# `api.py` looked like before the split; these are the deliberate additions since, and
# the test below is an equality, so a route added or removed without saying so fails.
ROUTES_AFTER_SPLIT = [
    "GET /api/admin/airtable/configured",
    "POST /api/admin/airtable/sync",
    "GET /ui/airtable",
    "POST /ui/airtable/sync",
    "POST /ui/airtable/rosfin",
    "GET /ui/airtable/officials",
    "GET /ui/airtable/officials.csv",
    "POST /ui/airtable/officials/add",
    "POST /ui/airtable/officials/add-suggested",
    "POST /ui/airtable/officials/deactivate",
    # The list itself, with the period filter and the file that goes with it.
    "GET /ui/rfm",
    "GET /ui/rfm/export.xlsx",
    # The whole base of the people as one Excel file, whatever the filters say.
    "GET /ui/people/export.xlsx",
    # The operator's «обработано» on a person of the result.
    "POST /ui/political/done",
    # «Мусор» for every held article of one story at once.
    "POST /ui/junk-holds/junk-all",
    # The nameless records of the operator's base against the list.
    "GET /ui/base-unnamed",
    "POST /ui/base-unnamed/decide",
    # The graph of an investigation: the first answer and what a node adds.
    "GET /api/investigations/{key}/graph",
    "GET /api/investigations/{key}/graph/expand",
    # The React migration mirrors these read-only endpoints under /api/v1.
    "GET /api/v1/persons",
    "GET /api/v1/persons/{person_id}",
    "GET /api/v1/persons/{person_id}/aliases",
    "GET /api/v1/persons/{person_id}/persecution",
    "GET /api/v1/persons/{person_id}/events",
    "GET /api/v1/persons/{person_id}/detail",
    "GET /api/v1/articles/{article_id}",
    "GET /api/v1/candidates",
    "GET /api/v1/rosfinmonitoring/snapshots",
    "GET /api/v1/rosfinmonitoring/snapshots/{snapshot_id}",
    "GET /api/v1/rosfinmonitoring/snapshots/{snapshot_id}/entries",
    "GET /api/v1/operations/runs",
    "GET /api/v1/operations/runs/{run_id}",
    "GET /api/v1/monitoring/status",
    "GET /api/v1/monitoring/runs",
    "GET /api/v1/monitoring/runs/{run_id}",
    "GET /api/v1/monitoring/findings",
    "GET /api/v1/health",
    "GET /api/v1/health/live",
    "GET /api/v1/health/ready",
    "GET /api/v1/about",
    "GET /api/v1/status",
    "GET /api/v1/entities",
    "GET /api/v1/publications",
    "GET /api/v1/articles/{article_id}/mentions",
    "GET /api/v1/political",
    "GET /api/v1/investigations",
    "GET /api/v1/investigations/{key}",
    "GET /api/v1/sentences",
]
ALL_ROUTES = sorted(ROUTES_BEFORE_SPLIT + ROUTES_AFTER_SPLIT)
# Only the shared dependencies module may build the engine and the session factory.
ENGINE_FACTORIES = {
    "create_database_engine",
    "create_session_factory",
    "create_engine",
    "sessionmaker",
}


def _routes(routes: Iterable[BaseRoute]) -> Iterator[APIRoute]:
    for route in routes:
        # FastAPI's included-router wrapper applies a prefix in its effective
        # candidates. Unwrapping original_router directly loses that prefix.
        effective_candidates = getattr(route, "effective_candidates", None)
        if effective_candidates is not None:
            yield from _routes(effective_candidates())
        elif isinstance(getattr(route, "original_route", route), APIRoute):
            yield cast(APIRoute, route)


def _method_paths() -> list[str]:
    return [
        f"{method} {route.path}" for route in _routes(app.routes) for method in route.methods or ()
    ]


def test_every_route_of_the_monolith_still_exists() -> None:
    """Exactly the pre-split routes plus the ones added since, named in
    `ROUTES_AFTER_SPLIT`: a route that disappeared, was renamed or appeared unnoticed
    fails here."""
    assert sorted(_method_paths()) == ALL_ROUTES


def test_no_method_and_path_is_served_twice() -> None:
    duplicates = [key for key, count in Counter(_method_paths()).items() if count > 1]

    assert duplicates == []


def _run(code: str, *, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env={"PYTHONPATH": str(SRC), "PATH": "/usr/bin:/bin", **env},
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def test_the_app_imports_without_a_database_and_without_network() -> None:
    """No DATABASE_URL, and any socket opened while importing fails the import."""
    code = (
        "import socket\n"
        "def refuse(*args, **kwargs):\n"
        "    raise AssertionError('network I/O while creating the app')\n"
        "socket.socket.connect = refuse\n"
        "socket.create_connection = refuse\n"
        "import api\n"
        "assert api.app.title == 'Court Monitor API'\n"
        "print('ok')\n"
    )

    result = _run(code, env={})

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"


def test_startup_runs_no_migration(monkeypatch: pytest.MonkeyPatch) -> None:
    import alembic.command

    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("the API must not migrate the database")

    for name in ("upgrade", "downgrade", "stamp"):
        monkeypatch.setattr(alembic.command, name, refuse)
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://user:secret@127.0.0.1:1/unused")

    with TestClient(app) as client:
        assert client.get("/health/live").status_code == 200


def _calls(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Call):
            func = node.func
            names.add(func.id if isinstance(func, ast.Name) else getattr(func, "attr", ""))
    return names


@pytest.mark.parametrize(
    "module",
    sorted((SRC / "web/routers").glob("*.py")) + sorted((SRC / "web/ui").glob("*.py")),
    ids=lambda path: f"{path.parent.name}/{path.name}",
)
def test_route_modules_build_no_engine_or_session_factory(module: Path) -> None:
    assert _calls(module) & ENGINE_FACTORIES == set()
