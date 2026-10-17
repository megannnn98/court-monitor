"""The evaluation tooling's command line (`court-evaluation`, `evaluation.cli`): the
commands it took over from `python src/main.py`, their help offline, and the evaluations
with a disposable database never reaching DATABASE_URL."""

from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys

import pytest
from support.workspace import REPOSITORY

from evaluation.cli import build_parser, main

# In the order they had in the application's `--help`.
COMMANDS = (
    "evaluate-er",
    "evaluate-final",
    "build-real-world-corpus",
    "real-world-corpus-status",
    "real-world-golden",
    "evaluate-real-world",
    "compare-entity-models",
)
# Commands that run on their own disposable database (EVALUATION_DATABASE_URL).
DISPOSABLE_DATABASE_COMMANDS = ("evaluate-er", "evaluate-final")


def _subparsers() -> dict[str, argparse.ArgumentParser]:
    parser = build_parser()
    action = next(action for action in parser._actions if action.dest == "command")
    return dict(action.choices)  # type: ignore[arg-type]


@pytest.fixture
def offline(monkeypatch: pytest.MonkeyPatch) -> None:
    """No DATABASE_URL and no network: any connection attempt fails the test."""

    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("network I/O")

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


def test_the_tooling_has_every_evaluation_command_with_a_handler() -> None:
    subparsers = _subparsers()

    assert tuple(subparsers) == COMMANDS
    for command, parser in subparsers.items():
        assert callable(parser.get_default("handler")), command


@pytest.mark.parametrize("command", COMMANDS)
def test_every_command_help_works_offline(command: str, offline: None) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main([command, "--help"])

    assert exit_info.value.code == 0


@pytest.mark.parametrize("command", DISPOSABLE_DATABASE_COMMANDS)
def test_evaluations_never_reach_the_working_database(
    command: str, monkeypatch: pytest.MonkeyPatch, offline: None
) -> None:
    """With DATABASE_URL set and no evaluation database, they refuse instead of using it."""
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://user:secret@127.0.0.1:1/court_monitor")
    monkeypatch.delenv("EVALUATION_DATABASE_URL", raising=False)

    with pytest.raises(SystemExit) as exit_info:
        main([command])

    assert "EVALUATION_DATABASE_URL" in str(exit_info.value.code)


@pytest.mark.parametrize(
    "launcher", [["court-evaluation"], [sys.executable, "-m", "evaluation"]], ids=str
)
def test_the_installed_command_runs(launcher: list[str]) -> None:
    # The console script of the installed distribution, and `python -m evaluation`.
    env = {key: value for key, value in os.environ.items() if key != "DATABASE_URL"}
    if launcher[0] == "court-evaluation":
        launcher = [os.path.join(os.path.dirname(sys.executable), "court-evaluation")]
    result = subprocess.run(
        [*launcher, "--help"],
        cwd=REPOSITORY,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "evaluate-final" in result.stdout
