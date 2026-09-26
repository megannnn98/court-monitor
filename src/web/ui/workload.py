"""What waits for the operator: the pairs to decide and the unclear answers of the steps.

Every page shows the size of this queue in the menu; «Очередь» shows its items. Nothing
here decides anything: the pairs come from `entities.disputes`, the rest from what
steps 5 and 6 wrote.
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
    # Unnamed figurants neither identified nor closed as «nobody on the list».
    unnamed: int = 0

    @property
    def total(self) -> int:
        return self.pairs + self.unclear_roles + self.unclear_verdicts + self.unnamed


_OPEN_UNNAMED = text(
    """
    SELECT count(*) FROM unnamed_figurants f
    WHERE NOT EXISTS (
        SELECT 1 FROM unnamed_decisions d
        WHERE d.figurant_key = f.key AND d.decision IN ('same', 'none')
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
    )
