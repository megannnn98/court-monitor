"""What this instance is running: the code it was built from, and what it talks to.

An operator looking at a number in the console has no way to tell whether it came from
the code in the repository. So the build stamps itself, and the console says so in one
place: `/ui/about`.

The commit comes from the build, not from git at runtime: the image carries no `.git`
(only `src`, `migrations` and `docs/wiki` are copied in), and adding one to a private
deployment image to read a string out of it is not worth the surface. `BUILD_COMMIT`,
`BUILD_TIME` and `BUILD_TAG` are passed as build args, and every field degrades to «неизвестно» when a
developer runs from a working copy.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

APP_VERSION = "1.0.0"
UNKNOWN = "неизвестно"


@dataclass(frozen=True)
class BuildInfo:
    """What the running image was built from, as far as it can be told."""

    commit: str
    built_at: str
    version: str
    # `git describe --tags --always` at build time: «0.36.0» on a release, «0.36.0-3-g495d9e9»
    # three commits after one.
    tag: str = UNKNOWN

    def rows(self) -> list[tuple[str, str]]:
        return [
            ("Версия приложения", self.version),
            ("Тег", self.tag),
            ("Коммит", self.commit),
            ("Собран", self.built_at),
        ]


def build_info(env: Mapping[str, str] | None = None) -> BuildInfo:
    """The build stamp, read from the environment the image was started with."""
    env = os.environ if env is None else env
    commit = (env.get("BUILD_COMMIT") or "").strip()
    built_at = (env.get("BUILD_TIME") or "").strip()
    tag = (env.get("BUILD_TAG") or "").strip()
    return BuildInfo(
        commit=commit[:12] if commit else UNKNOWN,
        built_at=built_at or UNKNOWN,
        version=APP_VERSION,
        tag=tag or UNKNOWN,
    )
