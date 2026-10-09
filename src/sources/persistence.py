"""The persistence port lives in `monitor_core.ports`; re-exported for existing imports."""

from monitor_core.ports.persistence import IngestionPersistence

__all__ = ["IngestionPersistence"]
