"""Rosfinmonitoring snapshots and operator reference lists."""

from datetime import datetime

from pydantic import BaseModel, Field

from .common import OptionResponse


class RosfinmonitoringImportResponse(BaseModel):
    """What importing a published-list file did.

    `status` is `imported` (a new snapshot), `unchanged` (the file is the list already
    in the database, so nothing was written) or `error` (the file is not a list, or
    nothing was parsed out of it).
    """

    status: str
    snapshot_id: int | None = None
    snapshot_date: str | None = None
    entries: int = 0
    source_url: str
    detail: str | None = None
    # A new snapshot has no match rows yet, so the candidate query would find nothing
    # until the RF stage runs. The operator is told rather than left to notice.
    rematch_required: bool = False


class AirtableTableSyncResponse(BaseModel):
    """One list's outcome. `error` is set only when that list itself failed, so the
    other three still report what they did; `status` is `skipped` when the operator
    exported no file for it, which leaves the list alone rather than emptying it."""

    created: int
    updated: int
    unchanged: int
    errors: int
    received: int = 0
    status: str = "success"
    error: str | None = None
    # Rows the list no longer has, removed: it is a copy of an Airtable view, and this is
    # how many. Said out loud because a list that quietly shrank is one nobody trusts.
    removed: int = 0
    # Why nothing was removed, when the export was not believable enough to act on. The
    # operator needs this more than the count: it is the case where the button declined.
    removed_blocked: str | None = None


class AirtableSyncResponse(BaseModel):
    """The whole sync: success, partial (some list failed) or failed. `mode` says
    whether the records came from the Airtable API or from exported files."""

    status: str
    mode: str = "api"
    started_at: str
    finished_at: str | None = None
    duration_seconds: float = 0.0
    tables: dict[str, AirtableTableSyncResponse]


class RosfinmonitoringSnapshotResponse(BaseModel):
    """Rosfinmonitoring snapshot response model."""

    id: int
    snapshot_date: str
    source_url: str
    content_hash: str
    entry_count: int


class RosfinmonitoringEntryResponse(BaseModel):
    """Rosfinmonitoring entry response model."""

    id: int
    snapshot_id: int
    full_name: str
    normalized_name: str
    matching_key: str
    birth_date: str | None = None
    inclusion_reason: str | None = None


class ReferenceListResponse(BaseModel):
    """One reference list: what PostgreSQL holds of it and what it is read from."""

    name: str
    label: str
    count: int
    source: str


class OfficialSnapshotResponse(BaseModel):
    snapshot_id: int
    snapshot_date: datetime
    entry_count: int
    match_count: int


class AirtableOverviewResponse(BaseModel):
    """«База Airtable»: whether a sync can run and how, the lists, the published list."""

    configured: bool
    # Why a sync cannot run; empty when it can.
    reason: str
    mode_note: str
    missing: list[str]
    source_column: str
    lists: list[ReferenceListResponse]
    official: OfficialSnapshotResponse | None


class OfficialRowResponse(BaseModel):
    external_id: str
    full_name: str
    # The person the name landed on; empty when no article named them yet.
    entity_key: str
    entity_name: str
    category: str
    reason: str | None
    active: bool


class OfficialSuggestionResponse(BaseModel):
    key: str
    name: str
    category: str
    reason: str
    # Other entities that may be this person spelled otherwise; the pair review decides.
    possible_duplicates: list[str] = []


class OfficialsResponse(BaseModel):
    """The officials list kept by hand, and the model's suggestions for it."""

    total: int
    rows: list[OfficialRowResponse]
    suggestions: list[OfficialSuggestionResponse]
    categories: list[OptionResponse]


class OfficialAddRequest(BaseModel):
    full_name: str = Field(max_length=512)
    category: str = Field(default="other", max_length=32)
    reason: str = Field(default="", max_length=255)


class OfficialSuggestedRequest(BaseModel):
    key: str = Field(max_length=600)


class OfficialDeactivateRequest(BaseModel):
    external_id: str = Field(max_length=600)


class OfficialChangeResponse(BaseModel):
    # "added", "already" or "deactivated".
    status: str
