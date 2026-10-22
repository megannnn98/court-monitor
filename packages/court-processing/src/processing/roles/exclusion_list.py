"""The officials' exclusion list as the console reads and edits it (ADR 0031).

`excluded_persons` is processing's: step 4 reads it and adds the officials the texts name
by title. A person adds to it, switches a row off (never deletes it: «we looked and he is
not an official» is an answer) and accepts the model's suggestions here, in the caller's
transaction; the caller commits.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from processing.identity.disputes import (
    EntityRef,
    decided_pairs,
    find_pairs,
    pair_keys,
    resolve_keys,
)
from processing.identity.grouping import REGION_SEP
from processing.roles.officials import OFFICIAL_KINDS, official_entity_ids, official_marks
from processing.storage.models import EntityGroupRecord, EntityGroupRoleRecord, ExcludedPersonRecord

logger = logging.getLogger("web.ui.airtable")

Added = Literal["added", "already"]


@dataclass(frozen=True)
class Official:
    """A row of the list."""

    external_id: str
    full_name: str
    category: str | None
    reason: str | None
    active: bool


@dataclass(frozen=True)
class ListedOfficial:
    """A row of the list with the entity it landed on: blank when it fits nobody yet."""

    official: Official
    entity_key: str
    entity_name: str


@dataclass(frozen=True)
class Suggestion:
    """A person the model calls an official and the list does not name."""

    key: str
    name: str
    kind: str
    reason: str
    # The names of the other entities that may be this person spelled otherwise.
    possible_duplicates: tuple[str, ...] = ()


def _official(record: ExcludedPersonRecord) -> Official:
    return Official(
        record.external_id, record.full_name, record.category, record.reason, record.active
    )


def _groups(session: Session) -> tuple[dict[int, str], dict[int, str]]:
    groups = session.execute(
        select(EntityGroupRecord.id, EntityGroupRecord.key, EntityGroupRecord.name)
    ).all()
    return (
        {group_id: key for group_id, key, _ in groups},
        {group_id: name for group_id, _, name in groups},
    )


def listing(session: Session, *, limit: int) -> tuple[list[ListedOfficial], int]:
    """The first `limit` rows (the active first, then by name), with the name each one
    has actually landed on, and how many rows there are."""
    total = session.scalar(select(func.count()).select_from(ExcludedPersonRecord)) or 0
    rows = list(
        session.scalars(
            select(ExcludedPersonRecord)
            .order_by(ExcludedPersonRecord.active.desc(), ExcludedPersonRecord.full_name)
            .limit(limit)
        )
    )
    keys, names = _groups(session)
    landed = official_entity_ids(session, keys)
    # `landed` is keyed by the entity's id. The link needs the key, the person needs the
    # name they are filed under — those differ, since the key is folded.
    found = {
        record.external_id: (keys[group_id], names[group_id]) for group_id, record in landed.items()
    }
    return [
        ListedOfficial(_official(row), *found.get(row.external_id, ("", ""))) for row in rows
    ], int(total)


def every_official(session: Session) -> list[Official]:
    """The whole list, by name."""
    return [
        _official(row)
        for row in session.scalars(
            select(ExcludedPersonRecord).order_by(ExcludedPersonRecord.full_name)
        )
    ]


def suggestions(session: Session, *, limit: int) -> list[Suggestion]:
    """The people the model calls officials and the list does not name, by kind and name.
    Not offered: anyone the list names, active or switched off (that was an answer), and
    anyone with a mark of their own."""
    keys, names = _groups(session)
    named = official_entity_ids(session, keys, active_only=False)
    marks = official_marks(session, keys)
    found = []
    for role in session.scalars(
        select(EntityGroupRoleRecord).where(
            EntityGroupRoleRecord.method == "model",
            EntityGroupRoleRecord.kind.in_(sorted(OFFICIAL_KINDS)),
        )
    ):
        if role.group_id in named or role.group_id in marks or role.group_id not in keys:
            continue
        found.append(
            Suggestion(keys[role.group_id], names[role.group_id], role.kind or "", role.reason)
        )
    shown = sorted(found, key=lambda item: (item.kind, item.name))[:limit]
    duplicates = _possible_duplicates(session, [item.key for item in shown])
    return [replace(item, possible_duplicates=duplicates.get(item.key, ())) for item in shown]


def _possible_duplicates(session: Session, keys: list[str]) -> dict[str, tuple[str, ...]]:
    """For each key, the names of the entities of the same surname that the pair review
    would offer as the same person; a pair decided there is not offered."""
    if not keys:
        return {}
    surnames = {key.split(REGION_SEP)[0].split()[-1] for key in keys}
    refs = [
        EntityRef(id=row.id, key=row.key, name=row.name, mention_count=row.mention_count)
        for row in session.execute(
            select(
                EntityGroupRecord.id,
                EntityGroupRecord.key,
                EntityGroupRecord.name,
                EntityGroupRecord.mention_count,
            )
        )
        if row.key.split(REGION_SEP)[0].split()[-1] in surnames
    ]
    asked = set(keys)
    found: dict[str, list[str]] = {}
    decided = decided_pairs(session)
    for pair in find_pairs(refs, decided):
        for mine, other in ((pair.left, pair.right), (pair.right, pair.left)):
            if mine.key in asked:
                found.setdefault(mine.key, []).append(other.name)
    # Today's keys, as `find_pairs` reads them: a decision made before a surname was cut
    # another way still holds.
    today = set(resolve_keys(decided, session.scalars(select(EntityGroupRecord.key))))
    for key, names in _name_and_patronymic(session, keys, today).items():
        shown = found.setdefault(key, [])
        shown.extend(name for name in names if name not in shown)
    return {key: tuple(names) for key, names in found.items()}


def _name_and_patronymic(
    session: Session, keys: list[str], decided: set[tuple[str, str]]
) -> dict[str, list[str]]:
    """«Алексей Михайлович» and «Алексей Михайлович Становский»: the first has the
    patronymic where a surname stands, so the pair review, which pairs by surname, never
    sees the two. Each is named to the other; nothing is merged — a name and a patronymic
    alone are any number of people."""
    found: dict[str, list[str]] = {}
    for key in keys:
        words = key.split(REGION_SEP)[0].split()
        if len(words) < 2:
            continue
        bare = " ".join(words[:2])
        if len(words) == 2:
            # The bare name's own card, with a region or not, is the same entity.
            related = EntityGroupRecord.key.startswith(f"{bare} ", autoescape=True)
        else:
            related = (EntityGroupRecord.key == bare) | EntityGroupRecord.key.startswith(
                f"{bare}{REGION_SEP}", autoescape=True
            )
        for row in session.execute(
            select(EntityGroupRecord.key, EntityGroupRecord.name).where(related)
        ):
            if row.key != key and pair_keys(key, row.key) not in decided:
                found.setdefault(key, []).append(row.name)
    return found


def _normalized(name: str) -> str:
    return " ".join(name.casefold().replace("ё", "е").split())


def add(session: Session, full_name: str, category: str, reason: str) -> Added:
    """Add a person to the list; "already" for a name it holds. ValueError for no name."""
    name = " ".join(full_name.split())
    if not name:
        raise ValueError("no name")
    external_id = f"console:{name.casefold()}"
    existing = session.scalar(
        select(ExcludedPersonRecord).where(ExcludedPersonRecord.external_id == external_id)
    )
    if existing is not None:
        return "already"
    session.add(
        ExcludedPersonRecord(
            external_id=external_id,
            full_name=name,
            normalized_name=_normalized(name),
            category=category,
            reason=reason.strip() or None,
            active=True,
        )
    )
    logger.info("event=official_added name=%s category=%s", name, category)
    return "added"


def add_suggested(session: Session, key: str) -> Added | None:
    """Accept the model's suggestion; None for a key that is no suggestion, so nothing
    can put an arbitrary name on the list."""
    entity = session.scalar(select(EntityGroupRecord).where(EntityGroupRecord.key == key))
    role = session.get(EntityGroupRoleRecord, entity.id) if entity is not None else None
    if (
        entity is None
        or role is None
        or role.method != "model"
        or (role.kind or "") not in OFFICIAL_KINDS
    ):
        return None
    keys, _ = _groups(session)
    if entity.id in official_entity_ids(session, keys, active_only=False):
        return "already"
    name = " ".join(entity.name.split())
    session.add(
        ExcludedPersonRecord(
            external_id=f"model:{key}",
            full_name=name,
            normalized_name=_normalized(name),
            category=role.kind or "official",
            reason=f"предложено моделью, принято вручную: {role.reason}".strip(),
            active=True,
        )
    )
    logger.info("event=official_suggestion_accepted name=%s kind=%s", name, role.kind)
    return "added"


def deactivate(session: Session, external_id: str) -> bool:
    """Out of force, the row kept; False when the list has no such row."""
    row = session.scalar(
        select(ExcludedPersonRecord).where(ExcludedPersonRecord.external_id == external_id)
    )
    if row is None:
        return False
    row.active = False
    logger.info("event=official_deactivated name=%s", row.full_name)
    return True


def active_count(session: Session) -> int:
    """The rows of the list in force."""
    return int(
        session.scalar(
            select(func.count())
            .select_from(ExcludedPersonRecord)
            .where(ExcludedPersonRecord.active.is_(True))
        )
        or 0
    )
