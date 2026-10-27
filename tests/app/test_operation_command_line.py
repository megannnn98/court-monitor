"""The command line each operation runs, without a database: argv and refusals."""

from __future__ import annotations

import pytest

from orchestrator.operations import (
    OperationNotFoundError,
    OperationParameters,
    _command_for,
)

PROGRAM = ("python", "-m", "cli")


def command(name: str, **parameters: object) -> list[str]:
    return _command_for(name, OperationParameters.model_validate(parameters), PROGRAM)


@pytest.mark.parametrize(
    ("name", "parameters", "expected"),
    [
        ("monitor", {"mode": "purge"}, ["purge-junk"]),
        (
            "monitor",
            {"mode": "entities", "entity_model": "e/m", "decision_model": "d/m"},
            ["collect-entities", "--entity-model", "e/m", "--decision-model", "d/m"],
        ),
        ("monitor", {"mode": "rosfin", "entity_model": "e/m"}, ["check-entities-rosfin"]),
        (
            "monitor",
            {"mode": "figurants", "decision_model": "d/m"},
            ["find-figurants", "--decision-model", "d/m"],
        ),
        (
            "monitor",
            {"mode": "political", "entity_model": "e/m"},
            ["find-political", "--entity-model", "e/m"],
        ),
        (
            "monitor",
            {"mode": "resolve", "sources": ["ovd-info", "kommersant", "ovd-info"]},
            ["monitor-resolve", "--selected-source", "ovd-info", "--selected-source", "kommersant"],
        ),
        ("monitor", {}, ["monitor", "--catch-up"]),
        (
            "monitor",
            {"source": "memopzk-figurants"},
            ["monitor", "--catch-up", "--source", "memopzk-figurants"],
        ),
        (
            "monitor",
            {
                "mode": "load",
                "sources": ["kommersant", "kommersant", "ovd-info"],
                "published_from": "2026-10-01",
                "published_to": "2026-10-02",
                "limit": 5,
                "entity_model": "e/m",
            },
            [
                "monitor",
                "--catch-up",
                "--selected-source",
                "kommersant",
                "--selected-source",
                "ovd-info",
                "--load-only",
                "--published-from",
                "2026-10-01",
                "--published-to",
                "2026-10-02",
                "--limit",
                "5",
            ],
        ),
        (
            "discover-and-ingest",
            {"source": "ovd-info", "limit": 3},
            ["discover-and-ingest", "--source", "ovd-info", "--limit", "3"],
        ),
        ("extract-entities", {"limit": 3}, ["extract-entities", "--limit", "3"]),
        (
            "extract-entities",
            {"source": "ovd-info", "limit": 3},
            ["extract-entities", "--source", "ovd-info", "--limit", "3"],
        ),
        ("resolve-people", {"limit": 3}, ["resolve-people", "--limit", "3", "--workers", "1"]),
        (
            "resolve-people",
            {"limit": 3, "workers": 4},
            ["resolve-people", "--limit", "3", "--workers", "4"],
        ),
        ("classify-persecution", {"limit": 3}, ["classify-persecution", "--limit", "3"]),
    ],
)
def test_command_line(name: str, parameters: dict[str, object], expected: list[str]) -> None:
    assert command(name, **parameters) == [*PROGRAM, *expected]


@pytest.mark.parametrize(
    ("name", "parameters", "message"),
    [
        (
            "monitor",
            {"mode": "purge", "sources": ["ovd-info"]},
            "purge takes no sources: it covers the whole database",
        ),
        (
            "monitor",
            {"mode": "entities", "source": "ovd-info"},
            "entities takes no sources: it covers the whole database",
        ),
        (
            "monitor",
            {"mode": "resolve", "source": "ovd-info"},
            "resolution takes sources, not source",
        ),
        ("monitor", {"mode": "resolve"}, "at least one news source is required"),
        ("monitor", {"mode": "resolve", "sources": []}, "at least one news source is required"),
        (
            "monitor",
            {"source": "ovd-info", "sources": ["ovd-info"]},
            "source and sources cannot both be set",
        ),
        (
            "monitor",
            {"sources": ["memopzk-figurants"]},
            "source is not a news source: memopzk-figurants",
        ),
        ("monitor", {"sources": ["nowhere"]}, "unknown source: nowhere"),
        ("monitor", {"source": "nowhere"}, "unknown source: nowhere"),
        ("discover-and-ingest", {"limit": 3}, "unknown source: None"),
        ("discover-and-ingest", {"source": "ovd-info"}, "limit is required"),
        ("extract-entities", {"source": "nowhere", "limit": 3}, "unknown source: nowhere"),
        ("resolve-people", {}, "limit is required"),
        ("classify-persecution", {}, "limit is required"),
    ],
)
def test_command_line_refused(name: str, parameters: dict[str, object], message: str) -> None:
    with pytest.raises(ValueError) as refused:
        command(name, **parameters)
    assert str(refused.value) == message


def test_unknown_operation() -> None:
    with pytest.raises(OperationNotFoundError):
        command("nothing")
