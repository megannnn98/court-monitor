"""What waits for the operator: the pairs to decide and the unclear answers of the steps.

Every page shows the size of this queue in the menu; «Очередь» shows its items. Nothing
here decides anything: the pairs come from `entities.disputes`, the rest from what
steps 4 and 5 wrote.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from db.orm_models import EntityGroupPoliticsRecord, EntityGroupRecord, EntityGroupRoleRecord
from entities.disputes import EntityRef, Pair, decided_pairs, find_pairs
from entities.politics import UNCLEAR as UNCLEAR_VERDICT
from entities.roles import UNCLEAR as UNCLEAR_ROLE


def dispute_pairs(db: Session) -> list[Pair]:
    """The pairs of entities that may be one person, not decided yet, most mentioned first."""
    refs = [
        EntityRef(id=row.id, key=row.key, name=row.name, mention_count=row.mention_count)
        for row in db.execute(
            select(
                EntityGroupRecord.id,
                EntityGroupRecord.key,
                EntityGroupRecord.name,
                EntityGroupRecord.mention_count,
            )
        ).all()
    ]
    return find_pairs(refs, decided_pairs(db))


@dataclass(frozen=True)
class Workload:
    pairs: int
    unclear_roles: int
    unclear_verdicts: int
    # Unnamed figurants with no identification yet; no_rf_match stays open.
    unnamed: int = 0
    junk_holds: int = 0

    @property
    def total(self) -> int:
        return (
            self.pairs + self.unclear_roles + self.unclear_verdicts + self.unnamed + self.junk_holds
        )


@dataclass(frozen=True)
class OperatorTask:
    """One manual queue, ready for the operator dashboard to present."""

    key: str
    title: str
    count: int
    href: str
    description: str


def operator_tasks(work: Workload) -> tuple[OperatorTask, ...]:
    """Manual queues in the one order the operator work cycle uses."""
    return (
        OperatorTask(
            "junk_holds",
            "Публикации на проверке",
            work.junk_holds,
            "/ui/junk-holds",
            "Проверьте, относятся ли удержанные публикации к отслеживаемым делам.",
        ),
        OperatorTask(
            "pairs",
            "Совпадения людей",
            work.pairs,
            "/ui/pairs",
            "Решите, относятся ли две записи к одному человеку.",
        ),
        OperatorTask(
            "roles",
            "Неясные роли",
            work.unclear_roles,
            "/ui/roles",
            "Проверьте роль человека в деле по досье и источникам.",
        ),
        OperatorTask(
            "politics",
            "Проверка политичности",
            work.unclear_verdicts,
            "/ui/politics-review",
            "Проверьте, относится ли дело к политически мотивированным.",
        ),
        OperatorTask(
            "unnamed",
            "Безымянные фигуранты",
            work.unnamed,
            "/ui/unnamed",
            "Установите человека, которого публикация не называет.",
        ),
    )


def next_operator_task(work: Workload) -> OperatorTask | None:
    """Highest-priority non-empty operator queue in work-cycle order."""
    return next((task for task in operator_tasks(work) if task.count), None)


_OPEN_UNNAMED = text(
    """
    SELECT count(*) FROM unnamed_figurants f
    WHERE NOT EXISTS (
        SELECT 1 FROM unnamed_identity_resolutions r
        WHERE r.figurant_key = f.key
          AND r.resolution IN ('rf_entry', 'existing_person', 'supplied_name', 'insufficient')
    )
    """
)


def workload(db: Session) -> Workload:
    unclear_roles = db.scalar(
        select(func.count())
        .select_from(EntityGroupRoleRecord)
        .where(EntityGroupRoleRecord.role == UNCLEAR_ROLE)
    )
    unclear_verdicts = db.scalar(
        select(func.count())
        .select_from(EntityGroupPoliticsRecord)
        .where(EntityGroupPoliticsRecord.verdict == UNCLEAR_VERDICT)
    )
    return Workload(
        len(dispute_pairs(db)),
        unclear_roles or 0,
        unclear_verdicts or 0,
        db.scalar(_OPEN_UNNAMED) or 0,
        db.scalar(text("SELECT count(*) FROM junk_screen_holds WHERE status = 'held'")) or 0,
    )
