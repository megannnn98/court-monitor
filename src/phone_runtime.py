"""Android/Termux lifecycle helpers that are inert on desktop deployments."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from collections.abc import Callable, Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path

logger = logging.getLogger("phone_runtime")

CommandLookup = Callable[[str], str | None]
CommandRunner = Callable[[list[str]], int]
BackupRunner = Callable[[list[str]], tuple[int, str, str]]


def _run_command(command: list[str]) -> int:
    return subprocess.run(command, check=False).returncode


@contextmanager
def termux_operation_wake_lock(
    *,
    env: Mapping[str, str] | None = None,
    find_command: CommandLookup = shutil.which,
    run_command: CommandRunner = _run_command,
) -> Iterator[None]:
    """Keep the CPU awake for one operation when running inside Termux.

    Failure to acquire the optional Android wake lock is observable but never blocks
    the operation. An unlock is sent only after a successful acquisition.
    """
    env = os.environ if env is None else env
    if not env.get("TERMUX_VERSION"):
        yield
        return
    acquire = find_command("termux-wake-lock")
    release = find_command("termux-wake-unlock")
    if acquire is None or release is None:
        logger.warning("event=termux_wake_lock_unavailable")
        yield
        return
    acquired = run_command([acquire]) == 0
    if not acquired:
        logger.warning("event=termux_wake_lock_failed")
    try:
        yield
    finally:
        if acquired and run_command([release]) != 0:
            logger.warning("event=termux_wake_unlock_failed")


def operation_wake_lock() -> AbstractContextManager[None]:
    """Default operation context used by the operator console."""
    return termux_operation_wake_lock()


def _run_backup_script(command: list[str]) -> tuple[int, str, str]:
    completed = subprocess.run(
        command,
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.returncode, completed.stdout, completed.stderr


def run_phone_backup(
    *,
    mode: str | None,
    stdout: str,
    stderr: str,
    env: Mapping[str, str] | None = None,
    script_path: str | Path | None = None,
    run_command: BackupRunner = _run_backup_script,
) -> tuple[str, str]:
    """Run the configured cloud backup after a successful final pipeline step.

    The primary operation's output is preserved and the backup status is appended as a
    structured log event. Desktop deployments and non-final steps are no-ops.
    """
    env = os.environ if env is None else env
    if mode != "political" or not env.get("TERMUX_VERSION"):
        return stdout, stderr
    path = (
        Path(script_path)
        if script_path is not None
        else Path(__file__).resolve().parents[1] / "phone" / "backup.sh"
    )
    try:
        returncode, backup_stdout, backup_stderr = run_command([str(path)])
    except OSError as exc:
        return stdout, _backup_log(stderr, "failed", str(exc))
    status = "succeeded" if returncode == 0 else "failed"
    details = "\n".join(part for part in (backup_stdout.strip(), backup_stderr.strip()) if part)
    return stdout, _backup_log(stderr, status, details)


def _backup_log(stderr: str, status: str, details: str) -> str:
    separator = "" if not stderr or stderr.endswith("\n") else "\n"
    suffix = f"{separator}event=phone_backup status={status}\n"
    return stderr + suffix + (details + "\n" if details else "")
