"""Commands whose arguments and execution already live in their area's own `cli` module.

`register_group` calls the area's `add_*_arguments` and binds every command it added to
one handler, so the area module stays the single place those commands are defined.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable

from cli.context import CliContext

Handler = Callable[[argparse.Namespace, CliContext], None]


def register_group(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    add_arguments: Callable[[argparse._SubParsersAction[argparse.ArgumentParser]], object],
    handler: Handler,
    *,
    overrides: dict[str, Handler] | None = None,
) -> None:
    """Register an area's commands; `overrides` gives some of them their own handler."""
    before = set(subparsers.choices)
    add_arguments(subparsers)
    for name in sorted(set(subparsers.choices) - before):
        chosen = (overrides or {}).get(name, handler)
        subparsers.choices[name].set_defaults(handler=chosen)
