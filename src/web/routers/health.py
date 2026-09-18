"""Liveness and readiness."""

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from health import (
    LivenessReport,
    ReadinessChecker,
    ReadinessReport,
    ReadinessStatus,
)
from web.dependencies import get_readiness_checker

router = APIRouter()


# Health endpoints (ADR 0014)
@router.get("/health")
def health_check() -> dict[str, str]:
    """Liveness (kept for compatibility; prefer /health/live)."""
    return {"status": "ok"}


@router.get("/health/live", response_model=LivenessReport)
def health_live() -> LivenessReport:
    """The process answers. Never checks dependencies."""
    return LivenessReport()


@router.get(
    "/health/ready",
    response_model=ReadinessReport,
    responses={503: {"model": ReadinessReport}},
)
def health_ready(
    checker: ReadinessChecker = Depends(get_readiness_checker),  # noqa: B008
) -> ReadinessReport | JSONResponse:
    """Database and schema are required; Qdrant, Together AI and monitoring are reported."""
    report = checker.check()
    if report.status is ReadinessStatus.UNAVAILABLE:
        return JSONResponse(status_code=503, content=report.model_dump(mode="json"))
    return report
