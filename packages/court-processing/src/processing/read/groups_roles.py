"""Entity groups, identities, role queues and list filters."""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from sqlalchemy import ColumnElement, Text, cast, exists, func, literal, or_, select, text
from sqlalchemy.orm import InstrumentedAttribute, Session

from db.arrays import any_of, any_text
from processing.identity.disputes import EntityRef
from processing.storage.models import (
    EntityGroupChargeRecord,
    EntityGroupRecord,
    EntityGroupRoleRecord,
)


class GroupIdentity(Protocol):
    """Stable identity required when applying a decision, without an ORM dependency."""

    @property
    def id(self) -> int: ...

    @property
    def key(self) -> str: ...


def group_references(session: Session) -> list[EntityRef]:
    """Lightweight references for identity comparisons, with no evidence payloads."""
    return [
        EntityRef(id=id_, key=key, name=name, mention_count=count)
        for id_, key, name, count in session.execute(
            select(
                EntityGroupRecord.id,
                EntityGroupRecord.key,
                EntityGroupRecord.name,
                EntityGroupRecord.mention_count,
            )
        )
    ]


def references_by_mentions(
    session: Session, ids: Collection[int], *, limit: int
) -> list[EntityRef]:
    """Review candidates sorted by frequency and database key collation, under LIMIT."""
    if not ids:
        return []
    return [
        EntityRef(id=id_, key=key, name=name, mention_count=count)
        for id_, key, name, count in session.execute(
            select(
                EntityGroupRecord.id,
                EntityGroupRecord.key,
                EntityGroupRecord.name,
                EntityGroupRecord.mention_count,
            )
            .where(any_of(EntityGroupRecord.id, ids))
            .order_by(EntityGroupRecord.mention_count.desc(), EntityGroupRecord.key)
            .limit(limit)
        )
    ]


def role_review(session: Session, *, role: str, limit: int) -> list[tuple[str, str, str]]:
    """(key, name, reason) for one role's review queue, most mentioned first."""
    return [
        (key, name, reason)
        for key, name, reason in session.execute(
            select(EntityGroupRecord.key, EntityGroupRecord.name, EntityGroupRoleRecord.reason)
            .join(EntityGroupRoleRecord, EntityGroupRoleRecord.group_id == EntityGroupRecord.id)
            .where(EntityGroupRoleRecord.role == role)
            .order_by(EntityGroupRecord.mention_count.desc(), EntityGroupRecord.key)
            .limit(limit)
        )
    ]


def group_count(session: Session) -> int:
    """All current entity groups, whatever their role or classification."""
    return session.scalar(select(func.count()).select_from(EntityGroupRecord)) or 0


def role_count(session: Session, *, role: str) -> int:
    """Groups assigned the given role by the roles step or the operator."""
    return (
        session.scalar(
            select(func.count())
            .select_from(EntityGroupRoleRecord)
            .where(EntityGroupRoleRecord.role == role)
        )
        or 0
    )


@dataclass(frozen=True)
class Group:
    """An entity group as the rebuild left it."""

    id: int
    key: str
    name: str
    mention_count: int
    article_count: int
    last_published_at: datetime | None
    gender: str | None
    name_source: str
    collected_at: datetime
    # (form, count) of the name as written, most frequent first.
    variants: tuple[tuple[Any, ...], ...] = ()
    # (region, count), most frequent first.
    regions: tuple[tuple[Any, ...], ...] = ()
    event_types: Mapping[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class Role:
    """What step 4, or a person, decided a group is in its cases."""

    group_id: int
    role: str
    kind: str | None
    method: str
    reason: str
    quote: str


def _group(record: EntityGroupRecord) -> Group:
    return Group(
        id=record.id,
        key=record.key,
        name=record.name,
        mention_count=record.mention_count,
        article_count=record.article_count,
        last_published_at=record.last_published_at,
        gender=record.gender,
        name_source=record.name_source,
        collected_at=record.collected_at,
        variants=tuple(tuple(item) for item in record.variants or ()),
        regions=tuple(tuple(item) for item in record.regions or ()),
        event_types=dict(record.event_types or {}),
    )


def groups(session: Session, ids: Collection[int] | None = None) -> dict[int, Group]:
    """The groups asked for (None: every one), in id order."""
    if ids is not None and not ids:
        return {}
    query = select(EntityGroupRecord).order_by(EntityGroupRecord.id)
    if ids is not None:
        query = query.where(any_of(EntityGroupRecord.id, ids))
    return {record.id: _group(record) for record in session.scalars(query)}


def groups_by_key(session: Session, keys: Collection[str]) -> dict[str, Group]:
    """The groups of the keys asked for; a key no group has is left out."""
    if not keys:
        return {}
    return {
        record.key: _group(record)
        for record in session.scalars(
            select(EntityGroupRecord).where(any_text(EntityGroupRecord.key, keys, name="keys"))
        )
    }


def roles(session: Session, ids: Collection[int] | None = None) -> dict[int, Role]:
    """The roles of the groups asked for (None: every group with one)."""
    if ids is not None and not ids:
        return {}
    query = select(EntityGroupRoleRecord)
    if ids is not None:
        query = query.where(any_of(EntityGroupRoleRecord.group_id, ids))
    return {
        record.group_id: Role(
            group_id=record.group_id,
            role=record.role,
            kind=record.kind,
            method=record.method,
            reason=record.reason,
            quote=record.quote,
        )
        for record in session.scalars(query)
    }


def groups_with_role(session: Session, role: str) -> list[int]:
    """The groups of one role («figurant», «mentioned», …), in id order."""
    return list(
        session.scalars(
            select(EntityGroupRoleRecord.group_id)
            .where(EntityGroupRoleRecord.role == role)
            .order_by(EntityGroupRoleRecord.group_id)
        )
    )


# What parts a written name into words, besides a space (`groups_named_like`).
NAME_SEPARATORS = "-‐‑–—'’ʼ`\".,()«» \t"


def _spaced(column: InstrumentedAttribute[str]) -> ColumnElement[str]:
    """The name in SQL with a space before every word: « иван петров-водкин» becomes
    « иван петров водкин», so `LIKE '% stem%'` finds a word by its beginning."""
    folded = func.replace(func.lower(column), "ё", "е")
    return literal(" ").concat(func.translate(folded, NAME_SEPARATORS, " " * len(NAME_SEPARATORS)))


@dataclass(frozen=True)
class GroupHit:
    """A group found by its name: what a list of hits shows, no JSON of the group."""

    key: str
    name: str
    mention_count: int
    article_count: int
    last_published_at: datetime | None


def groups_named_like(session: Session, stems: Sequence[str], *, limit: int) -> list[GroupHit]:
    """The groups with a word of the name beginning with each stem (folded: lower case,
    «ё» as «е»), the most mentioned first. The whole filter runs in SQL, under its
    LIMIT, and only the columns of a hit are read."""
    if not stems:
        return []
    spaced = _spaced(EntityGroupRecord.name)
    return [
        GroupHit(*row)
        for row in session.execute(
            select(
                EntityGroupRecord.key,
                EntityGroupRecord.name,
                EntityGroupRecord.mention_count,
                EntityGroupRecord.article_count,
                EntityGroupRecord.last_published_at,
            )
            .where(*(spaced.like(f"% {stem}%") for stem in stems))
            .order_by(EntityGroupRecord.mention_count.desc(), EntityGroupRecord.key)
            .limit(limit)
        ).all()
    ]


def by_name(session: Session, ids: Collection[int]) -> list[int]:
    """The groups asked for, by name as the database orders it (its collation), then key."""
    if not ids:
        return []
    return list(
        session.scalars(
            select(EntityGroupRecord.id)
            .where(any_of(EntityGroupRecord.id, ids))
            .order_by(EntityGroupRecord.name, EntityGroupRecord.key)
        )
    )


def latest_first_order(session: Session, ids: Collection[int]) -> list[int]:
    """The groups asked for, the latest publication first (none known last), then by key
    as the database orders it."""
    if not ids:
        return []
    return list(
        session.scalars(
            select(EntityGroupRecord.id)
            .where(any_of(EntityGroupRecord.id, ids))
            .order_by(
                EntityGroupRecord.last_published_at.desc().nulls_last(), EntityGroupRecord.key
            )
        )
    )


# -- «Все люди»: the groups under the list's filters ---------------------------------


def filtered_group_ids(session: Session, *, q: str, article: str, region: str) -> list[int]:
    """The groups whose name or written form holds `q`, charged under `article`, of a
    region holding `region` (case-insensitive; an empty filter does not narrow)."""
    query = select(EntityGroupRecord.id)
    if q.strip():
        pattern = f"%{q.strip()}%"
        query = query.where(
            or_(
                EntityGroupRecord.name.ilike(pattern),
                cast(EntityGroupRecord.variants, Text).ilike(pattern),
            )
        )
    if article:
        query = query.where(
            exists().where(
                EntityGroupChargeRecord.group_id == EntityGroupRecord.id,
                EntityGroupChargeRecord.article == article,
            )
        )
    if region:
        query = query.where(cast(EntityGroupRecord.regions, Text).ilike(f"%{region}%"))
    return list(session.scalars(query))


def any_roles(session: Session) -> bool:
    """Whether step 4 has given anybody a role."""
    return bool(session.scalar(select(exists().select_from(EntityGroupRoleRecord))))


_ORDERS: dict[str, tuple[ColumnElement[Any], ...]] = {
    "mentions": (EntityGroupRecord.mention_count.desc(), EntityGroupRecord.key.asc()),
    "articles": (EntityGroupRecord.article_count.desc(), EntityGroupRecord.key.asc()),
    "recent": (
        EntityGroupRecord.last_published_at.desc().nulls_last(),
        EntityGroupRecord.key.asc(),
    ),
}


GROUP_ORDERS = tuple(_ORDERS)


def ordered_groups(
    session: Session,
    ids: Collection[int] | None,
    *,
    order: str,
    offset: int = 0,
    limit: int | None = None,
) -> list[Group]:
    """A page of the groups asked for (None: every group), in one of `GROUP_ORDERS` (the
    key, the tie-break, in the database's collation)."""
    if ids is not None and not ids:
        return []
    query = select(EntityGroupRecord).order_by(*_ORDERS[order]).offset(offset).limit(limit)
    if ids is not None:
        query = query.where(any_of(EntityGroupRecord.id, ids))
    return [_group(record) for record in session.scalars(query)]


def all_regions(session: Session) -> list[str]:
    """Every region a group is of, each once, in the database's collation."""
    return list(
        session.scalars(
            text(
                "SELECT DISTINCT region->>0 FROM entity_groups, "
                "jsonb_array_elements(regions) AS region ORDER BY 1"
            )
        )
    )


def groups_named(session: Session, part: str, *, limit: int) -> list[Group]:
    """The groups whose name holds `part` (case-insensitive), the most mentioned first,
    then by name in the database's collation."""
    return [
        _group(record)
        for record in session.scalars(
            select(EntityGroupRecord)
            .where(EntityGroupRecord.name.ilike(f"%{part}%"))
            .order_by(EntityGroupRecord.mention_count.desc(), EntityGroupRecord.name)
            .limit(limit)
        )
    ]


def by_key(session: Session, ids: Collection[int]) -> list[int]:
    """The groups asked for, by key as the database orders it (its collation)."""
    if not ids:
        return []
    return list(
        session.scalars(
            select(EntityGroupRecord.id)
            .where(any_of(EntityGroupRecord.id, ids))
            .order_by(EntityGroupRecord.key)
        )
    )


@dataclass(frozen=True)
class SelectionCounts:
    """What steps 2–4 left, for the selection funnel."""

    # The articles whose latest successful extraction found a criminal-case event: the
    # very ones step 3 collects people from and the purge keeps.
    criminal_publications: int
    # Held for a person to look at, and not a case by the latest extraction.
    held: int
    groups: int
    figurants: int
    officials: int


_SELECTION = text(
    """
    WITH latest AS (
        SELECT DISTINCT ON (article_id) id, article_id FROM article_extraction_runs
        WHERE status = 'succeeded' ORDER BY article_id, id DESC
    ),
    criminal AS (
        SELECT DISTINCT l.article_id FROM latest l
        JOIN extracted_events e ON e.extraction_run_id = l.id
        WHERE e.event_type = ANY(:criminal)
    )
    SELECT (SELECT count(*) FROM criminal),
           (SELECT count(*) FROM junk_screen_holds h
            WHERE h.status = :held AND h.article_id NOT IN (SELECT article_id FROM criminal)),
           (SELECT count(*) FROM entity_groups),
           (SELECT count(*) FROM entity_group_roles WHERE role = 'figurant'),
           (SELECT count(*) FROM entity_group_roles WHERE kind = ANY(:officials))
    """
)


def selection_counts(
    session: Session, *, criminal: Collection[str], held: str, officials: Collection[str]
) -> SelectionCounts:
    return SelectionCounts(
        *session.execute(
            _SELECTION,
            {"criminal": list(criminal), "held": held, "officials": sorted(officials)},
        ).one()
    )
