"""Android/Termux lifecycle helpers that are inert on desktop deployments."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from collections.abc import Callable, Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager

logger = logging.getLogger("phone_runtime")

CommandLookup = Callable[[str], str | None]
CommandRunner = Callable[[list[str]], int]


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
