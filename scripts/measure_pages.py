"""SQL statements and wall time of the console's main pages, on a database given.

    DATABASE_URL=postgresql+psycopg://…/court_monitor_test \\
        uv run python scripts/measure_pages.py [--repeat 5] [--json out.json]

Each page is asked once to warm up, then `--repeat` times: the statements are counted
on the last, the time is the median. Every request is a GET; nothing is written. Used
to compare a refactor before and after on the same data (docs/architecture/data-ownership.md).
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import Engine, event
from sqlalchemy.orm import Session

from db.database import create_database_engine, create_session_factory
from orchestrator.operations import OperationRegistry
from web.app import app
from web.dependencies import OPERATION_COMMAND_LINE, get_db, get_operation_registry

PAGES = (
    "/api/v1/cycle",
    "/ui/queue",
    "/api/v1/candidates/table",
    "/ui/candidates",
    "/api/v1/entities",
    "/ui/entities",
    "/ui/rfm",
    "/api/v1/sentences",
    "/ui/sentences",
    "/api/v1/overview",
    "/ui/overview",
    "/api/v1/political",
    "/api/v1/status",
    "/api/v1/about",
)


class _Counter:
    def __init__(self, engine: Engine) -> None:
        self.statements = 0
        event.listen(engine, "before_cursor_execute", self._count)

    def _count(self, *_: Any) -> None:
        self.statements += 1


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeat", type=int, default=5)
    parser.add_argument("--json", type=Path, default=None)
    args = parser.parse_args()
    engine = create_database_engine(os.environ["DATABASE_URL"])
    factory = create_session_factory(engine)
    counter = _Counter(engine)

    def session() -> Iterator[Session]:
        with factory() as db:
            yield db

    app.dependency_overrides[get_db] = session
    app.dependency_overrides[get_operation_registry] = lambda: OperationRegistry(
        factory, command_line=OPERATION_COMMAND_LINE, executor=lambda _work: None
    )
    results: dict[str, dict[str, float | int]] = {}
    with TestClient(app) as client:
        for page in PAGES:
            client.get(page)
            times = []
            for _ in range(args.repeat):
                counter.statements = 0
                started = time.perf_counter()
                response = client.get(page)
                times.append(time.perf_counter() - started)
            results[page] = {
                "status": response.status_code,
                "statements": counter.statements,
                "median_ms": round(statistics.median(times) * 1000, 1),
            }
            print(
                f"{page:32} {response.status_code} {counter.statements:5} SQL {results[page]['median_ms']:8.1f} ms"
            )
    if args.json is not None:
        args.json.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
