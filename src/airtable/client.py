"""The Airtable REST client: read every record of a table, and nothing else.

Only `GET` is ever issued — Airtable is a source here, never a target. The token goes
in the `Authorization` header and nowhere else: it is not logged, not put in a URL and
not returned in an error message.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from airtable.config import PAGE_SIZE, AirtableSettings

logger = logging.getLogger("airtable")

API_URL = "https://api.airtable.com"
_RECORDS_PATH = "/v0/{base_id}/{table_id}"


class AirtableError(RuntimeError):
    """Airtable refused, was unreachable or answered something unreadable.

    The message is safe to show an operator: it names the table and the status, never
    the token.
    """

    def __init__(self, message: str, *, table: str | None = None) -> None:
        super().__init__(message)
        self.table = table


@dataclass(frozen=True)
class AirtableRecord:
    """One record: its Airtable id and its fields, with the ones a list may not have
    treated as strings already pulled out."""

    id: str
    fields: Mapping[str, Any] = field(default_factory=dict)

    def text(self, *names: str) -> str:
        """The first of `names` present as a non-empty string, trimmed."""
        for name in names:
            value = self.fields.get(name)
            if isinstance(value, str) and value.strip():
                return value.strip()
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return str(value)
        return ""

    def flag(self, *names: str, default: bool = True) -> bool:
        """The first of `names` present as a checkbox; `default` when none is."""
        for name in names:
            value = self.fields.get(name)
            if isinstance(value, bool):
                return value
        return default


class AirtableClient(Protocol):
    """What the sync needs from Airtable. A fake implements this in tests."""

    def list_records(self, table: str) -> list[AirtableRecord]:
        """Every record of `table`, following Airtable's pagination.

        Raises `AirtableError` on any failure, including a failure on the tenth page:
        a half-read table is not a table, and the sync would write half of it.
        """


class HttpAirtableClient:
    def __init__(
        self,
        settings: AirtableSettings,
        *,
        client: httpx.Client | None = None,
    ) -> None:
        self._settings = settings
        self._client = client or httpx.Client(timeout=settings.timeout_seconds)

    def close(self) -> None:
        self._client.close()

    def list_records(self, table: str) -> list[AirtableRecord]:
        records: list[AirtableRecord] = []
        offset: str | None = None
        while True:
            params: dict[str, str] = {"pageSize": str(PAGE_SIZE)}
            if offset:
                params["offset"] = offset
            try:
                response = self._client.get(
                    API_URL + _RECORDS_PATH.format(base_id=self._settings.base_id, table_id=table),
                    params=params,
                    headers={"Authorization": f"Bearer {self._settings.token}"},
                )
            except httpx.HTTPError as exc:
                # The URL never carried the token, so the message is safe as it is.
                raise AirtableError(f"Airtable is unreachable: {exc}", table=table) from exc
            if response.status_code >= 400:
                raise AirtableError(
                    f"Airtable answered {response.status_code} for table {table!r}",
                    table=table,
                )
            try:
                page = response.json()
            except ValueError as exc:
                raise AirtableError(
                    f"Airtable answered unreadable JSON for table {table!r}", table=table
                ) from exc
            if not isinstance(page, dict):
                raise AirtableError(
                    f"Airtable answered an unexpected shape for table {table!r}", table=table
                )
            for raw in page.get("records") or ():
                if not isinstance(raw, dict) or not isinstance(raw.get("id"), str):
                    raise AirtableError(
                        f"Airtable sent a record without an id in table {table!r}", table=table
                    )
                fields = raw.get("fields")
                records.append(
                    AirtableRecord(
                        id=raw["id"],
                        fields=fields if isinstance(fields, dict) else {},
                    )
                )
            offset = page.get("offset")
            if not isinstance(offset, str) or not offset:
                return records
