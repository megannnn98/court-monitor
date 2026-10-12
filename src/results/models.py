"""What a monitoring finding looks like to its readers."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel


class MonitoringFindingStatus(StrEnum):
    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"


class MonitoringFindingView(BaseModel):
    id: int
    finding_type: str
    person_id: int
    criteria_version: str
    status: MonitoringFindingStatus
    active: bool
    first_seen_run_id: int | None
    first_seen_at: datetime
    last_seen_run_id: int | None
    last_seen_at: datetime
    inactive_since: datetime | None
    snapshot_id: int | None
    persecution_classification_id: int | None
    rosfin_match_id: int | None
