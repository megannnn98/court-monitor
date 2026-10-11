"""Processing: what the stored articles say, as mentions and events.

The public API of the module is `ProcessingService`. For now it is a facade over
`extraction` and stops at extraction: what is made of the mentions afterwards (canonical
persons, entity groups) is not decided here; see
`docs/architecture/modularization-plan.md`.
"""

from processing.service import PendingExtraction, ProcessingService

__all__ = ["PendingExtraction", "ProcessingService"]
