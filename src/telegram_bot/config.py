"""Telegram bot configuration: token, allowlist, timezone and result limits.

Fail-fast, in the style of `ApplicationSettings`: every problem is collected and
raised at once, so a misconfigured bot never starts half-working. The token is
never printed: `redacted()` replaces it with `***`.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_TIMEZONE = "Europe/Moscow"
DEFAULT_PEOPLE_LIMIT = 50
DEFAULT_PEOPLE_MAX_LIMIT = 200
REDACTED = "***"


class TelegramConfigurationError(ValueError):
    def __init__(self, problems: list[str]) -> None:
        super().__init__("Invalid Telegram bot configuration:\n- " + "\n- ".join(problems))
        self.problems = problems


@dataclass(frozen=True)
class TelegramBotSettings:
    token: str
    # Numeric Telegram user ids; a username may change, an id may not.
    allowed_user_ids: frozenset[int]
    timezone: ZoneInfo
    people_default_limit: int
    people_max_limit: int

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> TelegramBotSettings:
        env = os.environ if env is None else env
        problems: list[str] = []

        token = (env.get("TELEGRAM_BOT_TOKEN") or "").strip()
        if not token:
            problems.append("TELEGRAM_BOT_TOKEN environment variable is not set")

        allowed = _allowed_user_ids(env, problems)
        timezone = _timezone(env, problems)
        max_limit = _positive_int(
            env, "TELEGRAM_PEOPLE_MAX_LIMIT", DEFAULT_PEOPLE_MAX_LIMIT, problems
        )
        default_limit = _positive_int(
            env, "TELEGRAM_PEOPLE_DEFAULT_LIMIT", DEFAULT_PEOPLE_LIMIT, problems
        )
        if max_limit is not None and default_limit is not None and default_limit > max_limit:
            problems.append(
                "TELEGRAM_PEOPLE_DEFAULT_LIMIT must not be greater than TELEGRAM_PEOPLE_MAX_LIMIT"
            )

        if problems:
            raise TelegramConfigurationError(problems)
        assert allowed is not None and timezone is not None
        assert default_limit is not None and max_limit is not None
        return cls(
            token=token,
            allowed_user_ids=allowed,
            timezone=timezone,
            people_default_limit=default_limit,
            people_max_limit=max_limit,
        )

    def redacted(self) -> dict[str, Any]:
        """Configuration safe to print or log: no token, no personal data beyond ids."""
        return {
            "token": REDACTED,
            "allowed_user_ids_count": len(self.allowed_user_ids),
            "timezone": str(self.timezone),
            "people_default_limit": self.people_default_limit,
            "people_max_limit": self.people_max_limit,
        }


def _allowed_user_ids(env: Mapping[str, str], problems: list[str]) -> frozenset[int] | None:
    raw = (env.get("TELEGRAM_ALLOWED_USER_IDS") or "").strip()
    if not raw:
        # An empty allowlist would let anyone run the pipeline against the live database.
        problems.append(
            "TELEGRAM_ALLOWED_USER_IDS is empty: every command is closed by default, "
            "so the bot has nobody to serve"
        )
        return None
    ids: set[int] = set()
    for part in (piece.strip() for piece in raw.split(",")):
        if not part:
            continue
        try:
            ids.add(int(part))
        except ValueError:
            problems.append(f"TELEGRAM_ALLOWED_USER_IDS has a non-numeric id: {part!r}")
    if not ids and not problems:
        problems.append("TELEGRAM_ALLOWED_USER_IDS has no id")
    return frozenset(ids) if ids else None


def _timezone(env: Mapping[str, str], problems: list[str]) -> ZoneInfo | None:
    name = (env.get("TELEGRAM_BOT_TIMEZONE") or DEFAULT_TIMEZONE).strip()
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        problems.append(f"TELEGRAM_BOT_TIMEZONE is not a known timezone: {name!r}")
        return None


def _positive_int(
    env: Mapping[str, str], name: str, default: int, problems: list[str]
) -> int | None:
    raw = (env.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        problems.append(f"{name} must be an integer")
        return None
    if value < 1:
        problems.append(f"{name} must be greater than 0")
        return None
    return value
