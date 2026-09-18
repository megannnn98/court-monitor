"""Operator console: monitoring runs and findings."""

from html import escape

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from web.dependencies import get_db
from web.routers.monitoring import list_monitoring_findings, list_monitoring_runs
from web.ui.layout import _fmt, _page

router = APIRouter()


@router.get("/ui/monitoring")
def ui_monitoring(
    db: Session = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    runs = list_monitoring_runs(limit=20, offset=0, db=db)
    findings = list_monitoring_findings(active_only=True, limit=20, offset=0, db=db)
    run_rows = "".join(
        f"""<tr>
  <td><a href="/monitoring/runs/{run.id}">{run.id}</a></td>
  <td>{_fmt(run.scope)}</td>
  <td>{escape(run.status.value)}</td>
  <td>{_fmt(run.started_at)}</td>
  <td>{_fmt(run.duration_seconds)}</td>
</tr>"""
        for run in runs
    )
    finding_rows = "".join(
        f"""<tr>
  <td>{finding.id}</td>
  <td><a href="/ui/persons/{finding.person_id}">{finding.person_id}</a></td>
  <td>{escape(finding.finding_type)}</td>
  <td>{_fmt(finding.last_seen_at)}</td>
</tr>"""
        for finding in findings
    )
    return _page(
        "Monitoring",
        f"""<h2>Последние runs</h2>
<table><thead><tr><th>ID</th><th>Scope</th><th>Status</th><th>Started</th><th>Duration</th></tr></thead><tbody>{run_rows}</tbody></table>
<h2>Active findings</h2>
<table><thead><tr><th>ID</th><th>Person</th><th>Type</th><th>Last seen</th></tr></thead><tbody>{finding_rows}</tbody></table>""",
        active="monitoring",
        instruction="Здесь видно свежесть monitoring runs и actionable findings.",
        next_action="Если данные устарели, запустите нужную операцию на странице Operations.",
        db=db,
    )
