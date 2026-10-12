"""Ingestion: discover, fetch, parse and store the documents of a source.

The public API of the module is `ingestion.service.IngestionService`; the sources, their
parsers and the store are in `ingestion.sources`, over `monitor_core.ingestion`. See
`docs/architecture/modularization-plan.md`.

Nothing is imported here: `extraction` reads a source's name from this package, and
must not load every adapter to do it.
"""
