"""The CLI after splitting `main.py` into the `cli` package: every command's help works
offline, commands open the database only through the one composition root, and the
evaluations with a disposable database never reach DATABASE_URL."""

from __future__ import annotations

import argparse
import ast
import json
import os
import socket
import subprocess
import sys
from functools import cached_property
from pathlib import Path

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from cli.app import build_parser, main
from cli.context import CliContext

ROOT = Path(__file__).parents[2]
ENGINE_FACTORIES = {
    "create_database_engine",
    "create_session_factory",
    "create_engine",
    "sessionmaker",
}
# Commands that run on their own disposable database (EVALUATION_DATABASE_URL).
DISPOSABLE_DATABASE_COMMANDS = ("evaluate-er", "evaluate-retrieval", "evaluate-final")


def _subparsers() -> dict[str, argparse.ArgumentParser]:
    parser = build_parser()
    action = next(action for action in parser._actions if action.dest == "command")
    return dict(action.choices)  # type: ignore[arg-type]


def _commands() -> list[str]:
    return sorted(_subparsers())


@pytest.fixture
def offline(monkeypatch: pytest.MonkeyPatch) -> None:
    """No DATABASE_URL and no network: any connection attempt fails the test."""

    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("network I/O")

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


class UntouchableContext(CliContext):
    """A context whose database a command must never reach."""

    @cached_property
    def engine(self) -> Engine:
        raise AssertionError("the command reached the database engine")

    @cached_property
    def session_factory(self) -> sessionmaker[Session]:
        raise AssertionError("the command reached the database")


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    env = {key: value for key, value in os.environ.items() if key != "DATABASE_URL"}
    return subprocess.run(
        [sys.executable, "src/main.py", *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def test_main_help_runs_without_a_database() -> None:
    result = _run("--help")

    assert result.returncode == 0, result.stderr
    assert "validate-config" in result.stdout


def test_validate_config_runs_without_a_database() -> None:
    result = _run("validate-config")

    assert result.returncode == 0, result.stderr
    assert isinstance(json.loads(result.stdout), dict)


def test_every_command_is_registered_once_with_a_handler() -> None:
    subparsers = _subparsers()

    assert len(subparsers) == 34
    for command, parser in subparsers.items():
        assert callable(parser.get_default("handler")), command


@pytest.mark.parametrize("command", _commands())
def test_every_command_help_works_offline(command: str, offline: None) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main([command, "--help"], context=UntouchableContext())

    assert exit_info.value.code == 0


@pytest.mark.parametrize("command", DISPOSABLE_DATABASE_COMMANDS)
def test_evaluations_never_reach_the_working_database(
    command: str, monkeypatch: pytest.MonkeyPatch, offline: None
) -> None:
    """With DATABASE_URL set and no evaluation database, they refuse instead of using it."""
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://user:secret@127.0.0.1:1/court_monitor")
    monkeypatch.delenv("EVALUATION_DATABASE_URL", raising=False)

    with pytest.raises(SystemExit) as exit_info:
        main([command], context=UntouchableContext())

    assert "EVALUATION_DATABASE_URL" in str(exit_info.value.code)


def test_a_database_command_without_database_url_reports_it(offline: None) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["list-candidates", "--snapshot-id", "1"])

    assert "DATABASE_URL" in str(exit_info.value.code)


def test_the_context_is_the_only_place_that_builds_the_engine() -> None:
    for path in sorted((ROOT / "src/cli").glob("*.py")):
        if path.name == "context.py":
            continue
        calls = {
            node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
            if isinstance(node, ast.Call)
        }
        assert calls & ENGINE_FACTORIES == set(), path.name


def test_main_py_only_dispatches() -> None:
    tree = ast.parse((ROOT / "src/main.py").read_text(encoding="utf-8"))
    imported = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }

    assert imported == {"cli.app"}
