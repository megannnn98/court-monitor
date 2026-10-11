"""Ingestion: discover, fetch, parse and store the documents of a source.

The public API of the module is `IngestionService`. For now it is a facade over
`sources` and `monitor_core.ingestion`; see `docs/architecture/modularization-plan.md`.
"""

from ingestion.service import IngestionService, StoredDocuments

__all__ = ["IngestionService", "StoredDocuments"]
