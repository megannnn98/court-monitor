"""One state-derived next action shared by every operator page."""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.orm_models import OperatorOperationRunRecord
from operator_console import STALE_AFTER, OperationRunStatus
from web.ui.pipeline import STAGES, TITLES, pipeline_current
from web.ui.workload import Workload, next_operator_task, workload

_NEXT_STEP = {
    "load": "подгрузить статьи",
    "purge": "очистить от мусора",
    "entities": "собрать сущности",
    "figurants": "найти фигурантов",
    "political": "отобрать политические дела",
}


def live_next_action(db: Session, work: Workload | None = None) -> str:
    latest = db.scalars(
        select(OperatorOperationRunRecord)
        .where(OperatorOperationRunRecord.operation_name == "monitor")
        .order_by(OperatorOperationRunRecord.id.desc())
        .limit(1)
    ).first()
    mode = latest.parameters.get("mode") if latest is not None else None
    status = OperationRunStatus(latest.status) if latest is not None else None
    if latest is not None and status in (OperationRunStatus.PENDING, OperationRunStatus.RUNNING):
        last_sign_of_life = latest.heartbeat_at or latest.created_at
        if datetime.now(UTC) - last_sign_of_life > STALE_AFTER:
            status = OperationRunStatus.INTERRUPTED
    stage = pipeline_current(mode if isinstance(mode, str) else None, status)
    if status in (OperationRunStatus.PENDING, OperationRunStatus.RUNNING):
        title = TITLES.get(mode or "", TITLES[stage])
        return f"Дождитесь завершения шага «{title}» или остановите запуск."

    current_work = work or workload(db)
    if task := next_operator_task(current_work):
        return f"{task.title}: {task.count}."
    number = STAGES.index(stage) + 1
    return f"Шаг {number}: {_NEXT_STEP[stage]}."
