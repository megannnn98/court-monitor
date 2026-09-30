"""How Airtable is reached: the token, the base and the four tables.

Airtable is an editing surface for reference lists, nothing more. The token lives here
and nowhere else: it is never logged, never returned by the API and never given to the
browser.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

DEFAULT_TIMEOUT_SECONDS = 120.0
# Airtable's own page size; 15k records is ~150 requests, so the client follows offsets.
PAGE_SIZE = 100


class AirtableConfigurationError(ValueError):
    """Airtable is not configured, or is configured with blanks. Raised on use, not on
    start: the pipeline must not depend on Airtable being set up at all."""


@dataclass(frozen=True)
class AirtableSettings:
    token: str
    base_id: str
    sources_table: str
    rfm_persons_table: str
    known_persons_table: str
    excluded_persons_table: str
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> AirtableSettings:
        env = os.environ if env is None else env
        missing = [name for name in _REQUIRED if not (env.get(name) or "").strip()]
        if missing:
            raise AirtableConfigurationError(
                "Airtable is not configured: "
                + ", ".join(missing)
                + ". Set them in the environment to enable synchronization."
            )
        timeout = _positive_float(env, "AIRTABLE_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS)
        return cls(
            token=(env["AIRTABLE_TOKEN"] or "").strip(),
            base_id=(env["AIRTABLE_BASE_ID"] or "").strip(),
            sources_table=(env["AIRTABLE_SOURCES_TABLE"] or "").strip(),
            rfm_persons_table=(env["AIRTABLE_RFM_PERSONS_TABLE"] or "").strip(),
            known_persons_table=(env["AIRTABLE_KNOWN_PERSONS_TABLE"] or "").strip(),
            excluded_persons_table=(env["AIRTABLE_EXCLUDED_PERSONS_TABLE"] or "").strip(),
            timeout_seconds=timeout,
        )

    @classmethod
    def is_configured(cls, env: Mapping[str, str] | None = None) -> bool:
        try:
            cls.from_env(env)
        except AirtableConfigurationError:
            return False
        return True

    def redacted(self) -> dict[str, object]:
        """Configuration safe to log and to show: the token is never among it."""
        return {
            "configured": True,
            "base_id": self.base_id,
            "sources_table": self.sources_table,
            "rfm_persons_table": self.rfm_persons_table,
            "known_persons_table": self.known_persons_table,
            "excluded_persons_table": self.excluded_persons_table,
            "timeout_seconds": self.timeout_seconds,
        }


_REQUIRED = (
    "AIRTABLE_TOKEN",
    "AIRTABLE_BASE_ID",
    "AIRTABLE_SOURCES_TABLE",
    "AIRTABLE_RFM_PERSONS_TABLE",
    "AIRTABLE_KNOWN_PERSONS_TABLE",
    "AIRTABLE_EXCLUDED_PERSONS_TABLE",
)


def _positive_float(env: Mapping[str, str], name: str, default: float) -> float:
    raw = (env.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise AirtableConfigurationError(f"{name} must be a number, got {raw!r}") from exc
    if value <= 0:
        raise AirtableConfigurationError(f"{name} must be positive, got {raw!r}")
    return value
