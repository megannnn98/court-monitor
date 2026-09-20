"""The `ai_entity_review` monitoring stage: optional, counted, never fatal."""

from __future__ import annotations

from sqlalchemy.orm import Session, sessionmaker

from application import build_monitoring_service
from monitoring.models import MonitoringSettings, MonitoringTrigger
from persons.resolution.ai_review_service import EntityReviewBatchResult
from sources.source_registry import OVD_INFO


class StubReviewService:
    """An `AutomatedEntityReviewService` stand-in: no model, no database work."""

    def __init__(self, result: EntityReviewBatchResult | Exception) -> None:
        self._result = result
        self.limits: list[int] = []

    def review_pending(self, *, limit: int) -> EntityReviewBatchResult:
        self.limits.append(limit)
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


def _service(session_factory: sessionmaker[Session], entity_review: object | None) -> object:
    return build_monitoring_service(
        session_factory,
        settings=MonitoringSettings(enabled_sources=("ovd-info",), discovery_limit=7),
        env={},
        sources={OVD_INFO.name: OVD_INFO},
        use_env_semantic_indexer=False,
        entity_review=entity_review,  # type: ignore[arg-type]
    )


def test_the_stage_reports_not_configured_without_a_reviewer(
    session_factory: sessionmaker[Session],
) -> None:
    service = _service(session_factory, None)
    handle = service.start_derived_run(trigger=MonitoringTrigger.MANUAL)  # type: ignore[attr-defined]

    result = service.review_entities(handle)  # type: ignore[attr-defined]

    assert result.metrics == {"status": "not_configured"}
    assert result.processed == 0


def test_the_stage_records_what_the_review_decided(
    session_factory: sessionmaker[Session],
) -> None:
    reviewer = StubReviewService(
        EntityReviewBatchResult(
            reviewed=5, auto_accepted=2, auto_rejected=1, human_required=1, failed=1, skipped=3
        )
    )
    service = _service(session_factory, reviewer)
    handle = service.start_derived_run(trigger=MonitoringTrigger.MANUAL)  # type: ignore[attr-defined]

    result = service.review_entities(handle)  # type: ignore[attr-defined]
    run = service.repository.get_run(handle.run_id)  # type: ignore[attr-defined]

    # The stage reviews as many pending decisions as the run's own limit allows.
    assert reviewer.limits == [7]
    assert (result.processed, result.created, result.failed, result.reviews) == (5, 3, 1, 1)
    metrics = run.stage_metrics["ai_entity_review"]
    assert metrics["reviewed"] == 5
    assert metrics["auto_accepted"] == 2
    assert metrics["human_required"] == 1
    assert metrics["skipped"] == 3
