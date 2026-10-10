"""A person's word that an entity is nobody: it is taken out and stays out.

«Дмитрий Путин» is what the grouping made of «Дмитрий Песков» and «Путин» in one
sentence: there is no such person. A removal is kept by entity key, applied at once and
again at every rebuild; the publications and their mentions stay. Taking the word back
does not bring the entity back by itself: only the next rebuild gathers it again.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.orm_models import EntityGroupRecord, EntityRemovalRecord
from entities.disputes import KeyIndex
from entities.grouping import Entity


def removed_keys(session: Session) -> list[str]:
    return list(session.scalars(select(EntityRemovalRecord.key)))


def without_removed(entities: Sequence[Entity], removed: Iterable[str]) -> list[Entity]:
    """The rebuild's entities without the removed ones; a removal made before a card's
    region entered the key holds for the one entity of that name (`KeyIndex`)."""
    index = KeyIndex(entity.key for entity in entities)
    gone = {today for key in removed if (today := index.today(key)) is not None}
    return [entity for entity in entities if entity.key not in gone]


def remove(session: Session, entity: EntityGroupRecord) -> None:
    """Keep the removal and apply it now, in the caller's transaction: the entity leaves
    every list with all that was derived for it."""
    record = session.get(EntityRemovalRecord, entity.key)
    if record is None:
        session.add(EntityRemovalRecord(key=entity.key, name=entity.name))
    else:
        record.name = entity.name
    session.delete(entity)


def restore(session: Session, key: str) -> bool:
    """Take a removal back, in the caller's transaction; False when there was none. The
    entity is gathered again by the next rebuild."""
    record = session.get(EntityRemovalRecord, key)
    if record is None:
        return False
    session.delete(record)
    return True


def removals(session: Session) -> list[EntityRemovalRecord]:
    """Every removal, the latest first."""
    return list(
        session.scalars(
            select(EntityRemovalRecord).order_by(
                EntityRemovalRecord.decided_at.desc(), EntityRemovalRecord.key
            )
        )
    )
