"""A person's corrections of entity names: they win over the rules and the model.

«Лида Мониава» is how the news write her; «Лидия Мониава» is her name. A correction is
kept by entity key, applied at once and again at every rebuild; the key stays, so every
decision and mark on the entity keeps holding.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.orm_models import EntityGroupRecord, EntityNameOverrideRecord
from entities.disputes import KeyIndex
from entities.grouping import Entity, given_name_first

MANUAL = "manual"
MAX_NAME = 200


def clean_name(name: str) -> str:
    """One space between words, the given name first («Мониава Лидия» → «Лидия Мониава»,
    when the dictionary knows the given name)."""
    return given_name_first(" ".join(name.split()))[:MAX_NAME]


def name_overrides(session: Session) -> dict[str, str]:
    return {record.key: record.name for record in session.scalars(select(EntityNameOverrideRecord))}


def apply_overrides(entities: Sequence[Entity], overrides: Mapping[str, str]) -> list[Entity]:
    """The rebuild's entities with the corrected names; a correction made before a card's
    region entered the key holds for the one entity of that name."""
    index = KeyIndex(entity.key for entity in entities)
    names: dict[str, str] = {}
    for key, name in overrides.items():
        today = index.today(key)
        if today is not None:
            names[today] = name
    return [
        replace(entity, name=names[entity.key], name_source=MANUAL)
        if entity.key in names
        else entity
        for entity in entities
    ]


def correct_name(session: Session, entity: EntityGroupRecord, name: str) -> str:
    """Keep a correction and apply it now, in the caller's transaction; the name kept."""
    cleaned = clean_name(name)
    if not cleaned:
        raise ValueError("an empty name")
    record = session.get(EntityNameOverrideRecord, entity.key)
    if record is None:
        session.add(EntityNameOverrideRecord(key=entity.key, name=cleaned))
    else:
        record.name = cleaned
    entity.name, entity.name_source = cleaned, MANUAL
    return cleaned
