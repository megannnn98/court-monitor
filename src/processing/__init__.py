"""Processing: what the stored articles say, as mentions and events.

The public API of the module is `processing.service.ProcessingService`. For now it is a
facade over `processing.extraction` and stops at extraction: what is made of the
mentions afterwards (canonical persons, entity groups) is not decided here; see
`docs/architecture/modularization-plan.md`.

Nothing is imported here: a caller of one pure module of `processing.extraction` must
not load the pipeline and the ORM models to do it.
"""
