"""What a monitoring run asks of person resolution (ER v2).

ER v2 is the optional, frozen path of ADR 0024: it stays reachable so the monitoring
run, its findings and the person endpoints keep working, and it is not extended. This
is the one door a run uses; it adds nothing to what the services behind it do.
"""

from __future__ import annotations

from typing import Protocol

from persons.resolution.ai_review_service import (
    AutomatedEntityReviewService,
    EntityReviewBatchResult,
)
from persons.resolution.resolution_service import ExtractionResolutionService, ResolutionStats
from processing.extraction.pipeline import ExtractionVersions


class PendingResolution(Protocol):
    def extraction_runs_pending_resolution(
        self, *, source_base_url: str, versions: ExtractionVersions
    ) -> list[int]: ...


class PersonResolutionStage:
    def __init__(
        self,
        *,
        resolution: ExtractionResolutionService,
        pending: PendingResolution,
        versions: ExtractionVersions,
        # None: no AI reviewer configured (ENTITY_REVIEW_PROVIDER=none); every pending
        # decision goes straight to a human (ADR 0020).
        entity_review: AutomatedEntityReviewService | None = None,
    ) -> None:
        self._resolution = resolution
        self._pending = pending
        self._versions = versions
        self._entity_review = entity_review

    def runs_pending_resolution(self, *, source_base_url: str) -> list[int]:
        """Succeeded extraction runs of the source with an undecided person mention."""
        return self._pending.extraction_runs_pending_resolution(
            source_base_url=source_base_url, versions=self._versions
        )

    def resolve_run(self, extraction_run_id: int) -> ResolutionStats:
        return self._resolution.resolve_extraction_run(extraction_run_id)

    def review_pending(self, *, limit: int) -> EntityReviewBatchResult | None:
        """AI review of the pending decisions; None when no reviewer is configured."""
        if self._entity_review is None:
            return None
        return self._entity_review.review_pending(limit=limit)
