"""Read-only MCP adapter (stdio): a thin layer over `ReadOnlyPlatform`.

Run with the optional dependency group:

    uv run --group mcp python src/platform_api/mcp_server.py

Every tool is annotated read-only; there are no tools that ingest, merge,
review, delete or configure anything.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

if __package__ in (None, ""):  # executed as a script: make `src` importable
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from db.database import DatabasePoolSettings, create_database_engine, create_session_factory
from platform_api.read_only import PlatformError, ReadOnlyPlatform
from settings import ApplicationSettings

READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True)
TOOL_NAMES = (
    "research_people",
    "get_person",
    "get_research_report",
    "list_monitoring_findings",
    "get_monitoring_status",
)


def _error(exc: PlatformError) -> dict[str, Any]:
    return {"error": {"code": exc.code, "message": str(exc)}}


def build_mcp_server(platform: ReadOnlyPlatform) -> MCPServer:
    server: MCPServer = MCPServer(
        name="court-monitor",
        instructions=(
            "Read-only access to court-monitor research and monitoring results. "
            "Facts come from the deterministic domain services; every report claim "
            "cites stored evidence."
        ),
    )

    @server.tool(
        name="research_people",
        description=(
            "Structured research over canonical persons. `request` is a ResearchRequest: "
            '{"object_type": "person", "criteria": {...}, "limit": 1..100}. '
            "semantic_query is not supported here."
        ),
        annotations=READ_ONLY,
    )
    def research_people(request: dict[str, Any]) -> dict[str, Any]:
        try:
            return platform.research_people(request).model_dump(mode="json")
        except PlatformError as exc:
            return _error(exc)

    @server.tool(
        name="get_person",
        description="One canonical person with aliases, events, evidence and statuses.",
        annotations=READ_ONLY,
    )
    def get_person(person_id: int, snapshot_id: int | None = None) -> dict[str, Any]:
        try:
            return platform.get_person(person_id, snapshot_id).model_dump(mode="json")
        except PlatformError as exc:
            return _error(exc)

    @server.tool(
        name="get_research_report",
        description=(
            "Evidence-backed ResearchReport for a structured ResearchRequest "
            "(snapshot defaulting, review policy, citations). No language model."
        ),
        annotations=READ_ONLY,
    )
    def get_research_report(request: dict[str, Any]) -> dict[str, Any]:
        try:
            return platform.get_research_report(request).model_dump(mode="json")
        except PlatformError as exc:
            return _error(exc)

    @server.tool(
        name="list_monitoring_findings",
        description="Monitoring findings (actionable results), newest first; limit 1..100.",
        annotations=READ_ONLY,
    )
    def list_monitoring_findings(
        active_only: bool = True, limit: int = 50, offset: int = 0
    ) -> dict[str, Any]:
        try:
            return platform.list_monitoring_findings(
                active_only=active_only, limit=limit, offset=offset
            ).model_dump(mode="json")
        except PlatformError as exc:
            return _error(exc)

    @server.tool(
        name="get_monitoring_status",
        description="Running and latest monitoring runs, source checkpoints, active findings.",
        annotations=READ_ONLY,
    )
    def get_monitoring_status() -> dict[str, Any]:
        return platform.get_monitoring_status().model_dump(mode="json")

    return server


def main() -> None:
    settings = ApplicationSettings.from_env(os.environ)
    engine = create_database_engine(settings.database_url, DatabasePoolSettings.from_env())
    try:
        build_mcp_server(ReadOnlyPlatform(create_session_factory(engine))).run("stdio")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
