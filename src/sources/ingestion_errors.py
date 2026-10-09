"""The ingestion errors live in `monitor_core.errors`; re-exported for existing imports."""

from monitor_core.errors import (
    DiscoveryError,
    FetchError,
    IngestionError,
    ListingPageNotFoundError,
    NoTextError,
    ParseError,
    PermanentDiscoveryError,
    PermanentFetchError,
    PersistenceError,
    TransientDiscoveryError,
    TransientFetchError,
)

__all__ = [
    "DiscoveryError",
    "FetchError",
    "IngestionError",
    "ListingPageNotFoundError",
    "NoTextError",
    "ParseError",
    "PermanentDiscoveryError",
    "PermanentFetchError",
    "PersistenceError",
    "TransientDiscoveryError",
    "TransientFetchError",
]
