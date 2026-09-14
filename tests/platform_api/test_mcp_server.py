"""MCP adapter: tool schema, delegation, bounded responses, no write tools."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

pytest.importorskip("mcp.server.mcpserver", reason="install the `mcp` dependency group")

from sqlalchemy.orm import Session, sessionmaker
from support.monitoring_fixtures import (
    SIDOROV,
    FakeUpstream,
    build_service,
    import_rf_snapshot,
)

from platform_api.mcp_server import TOOL_NAMES, build_mcp_server
from platform_api.read_only import ReadOnlyPlatform

WRITE_VERBS = ("create", "delete", "merge", "update", "resolve", "apply", "ingest", "start", "run")


class _NoDatabasePlatform(ReadOnlyPlatform):
    def __init__(self) -> None:  # tools are listed without touching the database
        pass


def _tools() -> list[Any]:
    return asyncio.run(build_mcp_server(_NoDatabasePlatform()).list_tools())


def test_only_the_read_only_tools_are_exposed() -> None:
    tools = _tools()

    assert sorted(tool.name for tool in tools) == sorted(TOOL_NAMES)
    for tool in tools:
        assert tool.annotations is not None
        assert tool.annotations.read_only_hint is True
        assert tool.annotations.destructive_hint is False
        assert not any(tool.name.startswith(verb) for verb in WRITE_VERBS)


def test_tool_input_schemas() -> None:
    schemas = {tool.name: tool.input_schema for tool in _tools()}

    assert schemas["research_people"]["required"] == ["request"]
    assert schemas["get_person"]["required"] == ["person_id"]
    assert "limit" in schemas["list_monitoring_findings"]["properties"]


def _call(server: Any, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    result = asyncio.run(server.call_tool(name, arguments))
    assert not result.is_error, result
    return json.loads(result.content[0].text)  # type: ignore[no-any-return]


def test_tools_delegate_to_the_platform(session_factory: sessionmaker[Session]) -> None:
    import_rf_snapshot(session_factory, [("Петр Петров", "03.03.1970")])
    upstream = FakeUpstream()
    upstream.publish("sidorov", SIDOROV)
    build_service(session_factory, {"ovd-info": upstream}).run_source("ovd-info")
    server = build_mcp_server(ReadOnlyPlatform(session_factory))

    research = _call(
        server,
        "research_people",
        {"request": {"object_type": "person", "criteria": {"name": "Сидоров"}}},
    )
    report = _call(
        server,
        "get_research_report",
        {
            "request": {
                "object_type": "person",
                "criteria": {
                    "persecution_status": "political",
                    "rosfinmonitoring_status": "not_matched",
                },
            }
        },
    )
    findings = _call(server, "list_monitoring_findings", {"limit": 5})
    status = _call(server, "get_monitoring_status", {})
    too_many = _call(
        server, "research_people", {"request": {"object_type": "person", "limit": 500}}
    )

    assert [r["person"]["canonical_name"] for r in research["results"]] == ["Сергей Сидоров"]
    assert report["status"] == "completed"
    assert report["report"]["items"][0]["claims"]
    assert len(findings["findings"]) == 1
    assert status["active_findings"] == 1
    assert too_many == {
        "error": {"code": "limit_too_large", "message": too_many["error"]["message"]}
    }
