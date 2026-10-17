"""What waits for the operator: the pairs to decide and the unclear answers of the steps.

Every page shows the size of this queue in the menu; «Очередь» shows its items. Nothing
here decides anything: the pairs come from `processing.identity.disputes`, the rest from what
steps 4 and 5 wrote.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from analysis.politics.classification import UNCLEAR as UNCLEAR_VERDICT
from analysis.read import unidentified_figurant_count, verdict_count
from application import build_identity_commands
from processing.identity.service import Pair
from processing.read import group_references, held_article_count, role_count
from processing.roles.roles import UNCLEAR as UNCLEAR_ROLE


def dispute_pairs(db: Session) -> list[Pair]:
    """The pairs of entities that may be one person, not decided yet, most mentioned first."""
    return build_identity_commands().disputed_pairs(db, group_references(db))


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
            "Возможно мусор",
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


def workload(db: Session) -> Workload:
    return Workload(
        len(dispute_pairs(db)),
        role_count(db, role=UNCLEAR_ROLE),
        verdict_count(db, verdict=UNCLEAR_VERDICT),
        unidentified_figurant_count(db),
        held_article_count(db),
    )
