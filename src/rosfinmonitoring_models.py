"""Domain models for Rosfinmonitoring data."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field


class RosfinmonitoringEntryStatus(StrEnum):
    ACTIVE = "active"
    REMOVED = "removed"
    UNKNOWN = "unknown"


class RosfinmonitoringEntry(BaseModel):
    """Single entry from Rosfinmonitoring list."""

    model_config = ConfigDict(use_enum_values=False)

    id: int | None = None
    snapshot_id: int
    full_name: str
    normalized_name: str
    matching_key: str
    birth_date: datetime | None = None
    birth_place: str | None = None
    snils: str | None = None
    inn: str | None = None
    inclusion_reason: str | None = None
    inclusion_date: datetime | None = None
    status: RosfinmonitoringEntryStatus = RosfinmonitoringEntryStatus.ACTIVE
    raw_data: dict[str, Any] = Field(default_factory=dict)


class RosfinmonitoringSnapshot(BaseModel):
    """Snapshot of Rosfinmonitoring list at a point in time."""

    model_config = ConfigDict(use_enum_values=False)

    id: int | None = None
    snapshot_date: datetime
    source_url: str
    content_hash: str
    entry_count: int = 0
    fetched_at: datetime
    raw_content: bytes | None = None


class RosfinmonitoringIngestionResult(BaseModel):
    """Result of ingesting a Rosfinmonitoring snapshot."""

    snapshot_id: int
    entries_created: int
    entries_updated: int
    skipped_duplicates: int


class RosfinmonitoringParser(Protocol):
    """Protocol for parsing Rosfinmonitoring data."""

    def parse(self, raw_content: bytes) -> list[RosfinmonitoringEntry]: ...
