"""Domain enums and constants.

These are the only "shared vocabulary" allowed to cross layer boundaries.
Storage, sources, extraction and review layers import from here; the reverse
must never happen.
"""

from __future__ import annotations

from enum import StrEnum

# The ExtractedFact.field that names a person. Load-bearing: extractors write
# it, the ORM derives ``normalized_value`` from it, and matching selects on it.
# It lives here rather than next to any one of them because all three would
# otherwise have to import from each other's layer.
PERSON_NAME_FIELD = "full_name_original"

# Date tolerance for matching (in days). Used by case matching and search.
DATE_TOLERANCE_DAYS = 7


class VerificationStatus(StrEnum):
    """Status of an extracted value — see spec §3.1."""

    confirmed = "confirmed"  # stated directly by a reliable source
    inferred = "inferred"  # derived indirectly
    unverified = "unverified"  # from a source requiring verification
    conflicting = "conflicting"  # contradicts another source
    rejected = "rejected"  # recognised as erroneous


class EventType(StrEnum):
    """Court / case event types — spec §6.3."""

    case_opened = "case_opened"
    detained = "detained"
    preventive_measure_selected = "preventive_measure_selected"
    indictment_approved = "indictment_approved"
    case_received_by_court = "case_received_by_court"
    hearing_scheduled = "hearing_scheduled"
    trial_started = "trial_started"
    sentence_delivered = "sentence_delivered"
    appeal_filed = "appeal_filed"
    appeal_decided = "appeal_decided"
    cassation = "cassation"
    transfer = "transfer"
    release = "release"
    rfm_added = "rfm_added"
    rfm_removed = "rfm_removed"
    data_corrected = "data_corrected"
    unknown = "unknown"


class ParserStatus(StrEnum):
    """Processing state of a SourceDocument."""

    pending = "pending"
    parsed = "parsed"
    parser_failed = "parser_failed"
    skipped_duplicate = "skipped_duplicate"
    irrelevant = "irrelevant"


class SourceType(StrEnum):
    sudrf = "sudrf"
    rfm = "rfm"
    airtable = "airtable"
    telegram = "telegram"
    manual = "manual"


class SourceBackend(StrEnum):
    """How a source is reached."""

    fixture = "fixture"  # local HTML file(s) — tests / no-network mode
    http = "http"  # plain HTTP via httpx
    browser = "browser"  # Playwright fallback (not enabled in MVP)


class FetchHealth(StrEnum):
    """Outcome of a single fetch attempt."""

    ok = "ok"
    not_modified = "not_modified"
    http_error = "http_error"
    timeout = "timeout"
    blocked = "blocked"  # captcha / anti-bot / auth wall — do NOT bypass
    parser_failed = "parser_failed"
    duplicate = "duplicate"
