"""Automated monitoring: status, runs, findings."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from monitoring.findings import MonitoringFindingService
from monitoring.models import (
    MonitoringFindingView,
    MonitoringRunDetails,
    MonitoringRunStatus,
    MonitoringRunView,
    MonitoringStatusView,
)
from monitoring.repository import SqlAlchemyMonitoringRepository
from web.dependencies import get_db, session_factory_for

router = APIRouter()


def _monitoring_repository(db: Session) -> SqlAlchemyMonitoringRepository:
    return SqlAlchemyMonitoringRepository(session_factory_for(db))


@router.get("/monitoring/status", response_model=MonitoringStatusView)
def get_monitoring_status(
    db: Session = Depends(get_db),  # noqa: B008
) -> MonitoringStatusView:
    """Running and latest monitoring runs, source checkpoints, active findings."""
    return _monitoring_repository(db).status()


@router.get("/monitoring/runs", response_model=list[MonitoringRunView])
def list_monitoring_runs(
    limit: int = Query(default=20, ge=1, le=200),
    source: str | None = None,
    status: MonitoringRunStatus | None = None,
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),  # noqa: B008
) -> list[MonitoringRunView]:
    return _monitoring_repository(db).list_runs(
        limit=limit, offset=offset, source=source, status=status
    )


@router.get(
    "/monitoring/runs/{run_id}",
    response_model=MonitoringRunDetails,
    responses={404: {}},
)
def get_monitoring_run(
    run_id: int,
    db: Session = Depends(get_db),  # noqa: B008
) -> MonitoringRunDetails:
    """One run with its counters, stage metrics and failed items."""
    details = _monitoring_repository(db).get_run_details(run_id)
    if details is None:
        raise HTTPException(status_code=404, detail=f"Monitoring run {run_id} not found")
    return details


@router.get("/monitoring/findings", response_model=list[MonitoringFindingView])
def list_monitoring_findings(
    active_only: bool = True,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),  # noqa: B008
) -> list[MonitoringFindingView]:
    return MonitoringFindingService(session_factory_for(db)).list_findings(
        active_only=active_only, limit=limit, offset=offset
    )
