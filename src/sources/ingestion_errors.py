class IngestionError(Exception):
    """Expected failure while ingesting one source document."""


class FetchError(IngestionError):
    """Failed to fetch a source document."""


class TransientFetchError(FetchError):
    """Temporary fetch failure that may succeed on retry."""


class PermanentFetchError(FetchError):
    """Permanent fetch failure that should not be retried."""


class ParseError(IngestionError):
    """Failed to parse a source document."""


class PersistenceError(IngestionError):
    """Failed to persist an ingested document."""


class DiscoveryError(Exception):
    """Failed to discover source documents."""


class TransientDiscoveryError(DiscoveryError):
    """Temporary discovery failure that may succeed on retry."""


class PermanentDiscoveryError(DiscoveryError):
    """Permanent discovery failure that should not be retried."""
