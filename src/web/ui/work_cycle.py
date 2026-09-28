"""One state-derived next action shared by every operator page."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.orm_models import OperatorOperationRunRecord
from operator_console import OperationRunStatus
from web.ui.pipeline import STAGES, TITLES, pipeline_current
from web.ui.workload import Workload, workload

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
    stage = pipeline_current(mode if isinstance(mode, str) else None, status)
    if status in (OperationRunStatus.PENDING, OperationRunStatus.RUNNING):
        title = TITLES.get(mode or "", TITLES[stage])
        return f"Дождитесь завершения шага «{title}» или остановите запуск."

    current_work = work or workload(db)
    if stage == "entities" and current_work.junk_holds:
        return f"Проверить отсев: {current_work.junk_holds}."
    if stage == "figurants" and current_work.pairs:
        return f"Разобрать пары: {current_work.pairs}."
    number = STAGES.index(stage) + 1
    return f"Шаг {number}: {_NEXT_STEP[stage]}."
