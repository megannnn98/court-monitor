"""Entities that may be one person, for a person to decide: «Спорные случаи».

Splitting by patronymic keeps namesakes apart, and so also splits one person the
registry names in full and the news without a patronymic («Игорь Александрович Ранав»
and «Игорь Ранав»). A form of a given name the model left as written splits one too
(«Лида Мониава», «Лидия Мониава»). Two kinds of pair are offered:

- patronymic: one given name and surname, one entity with a patronymic, one without;
- similar: one surname, given names one letter or two apart at the end («Лида» and
  «Лидия», «Данил» and «Данила»), not of two genders.

«same» merges the two at once (`merge_groups`) and at every rebuild (`merge_decided`);
«different» takes the pair off the list. Decisions are kept by entity key.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.orm import Session

from db.orm_models import (
    EntityGroupChargeRecord,
    EntityGroupMentionRecord,
    EntityGroupRecord,
    EntityGroupRfMatchRecord,
    EntityGroupRoleRecord,
    EntityPairDecisionRecord,
)
from entities.grouping import Entity
from extraction.name_frequency import lookup_gender

SAME = "same"
DIFFERENT = "different"
PATRONYMIC = "patronymic"
SIMILAR = "similar"
# The stronger of two roles survives a merge.
_ROLE_RANK = {"figurant": 3, "possible": 2, "mentioned": 1, "unclear": 0}


@dataclass(frozen=True)
class EntityRef:
    id: int
    key: str
    name: str
    mention_count: int


@dataclass(frozen=True)
class Pair:
    kind: str
    left: EntityRef
    right: EntityRef

    @property
    def keys(self) -> tuple[str, str]:
        return pair_keys(self.left.key, self.right.key)


def pair_keys(first: str, second: str) -> tuple[str, str]:
    left, right = sorted((first, second))
    return left, right


def _similar_given(first: str, second: str) -> bool:
    """«лида» and «лидия», «данил» and «данила»: the shorter without its last letter
    begins the longer, at most two letters longer."""
    short, long = sorted((first, second), key=len)
    return (
        first != second
        and len(short) >= 3
        and len(long) - len(short) <= 2
        and long.startswith(short[:-1])
    )


def _gender(name: str) -> str | None:
    words = name.split()
    return lookup_gender(words[0]) if words else None


def find_pairs(
    entities: Iterable[EntityRef], decided: Iterable[tuple[str, str]] = ()
) -> list[Pair]:
    """The pairs to decide, most mentioned first; a decided pair is left out."""
    done = set(decided)
    by_name: dict[tuple[str, str], list[EntityRef]] = defaultdict(list)
    by_surname: dict[str, list[EntityRef]] = defaultdict(list)
    for entity in entities:
        words = entity.key.split()
        if len(words) < 2:
            continue
        by_name[words[0], words[-1]].append(entity)
        by_surname[words[-1]].append(entity)
    pairs: list[Pair] = []
    for group in by_name.values():
        bare = [entity for entity in group if len(entity.key.split()) == 2]
        full = [entity for entity in group if len(entity.key.split()) > 2]
        pairs += [Pair(PATRONYMIC, left, right) for left in bare for right in full]
    for group in by_surname.values():
        for index, left in enumerate(group):
            for right in group[index + 1 :]:
                left_words, right_words = left.key.split(), right.key.split()
                if not _similar_given(left_words[0], right_words[0]):
                    continue
                genders = {_gender(left.name), _gender(right.name)} - {None}
                middle = {tuple(left_words[1:-1]), tuple(right_words[1:-1])} - {()}
                # Two genders, or two patronymics: two people.
                if len(genders) > 1 or len(middle) > 1:
                    continue
                pairs.append(Pair(SIMILAR, left, right))
    pairs = [pair for pair in pairs if pair.keys not in done]
    return sorted(
        pairs,
        key=lambda pair: (-(pair.left.mention_count + pair.right.mention_count), pair.keys),
    )


def _keeps_first(first: tuple[str, int], second: tuple[str, int]) -> bool:
    """Which of two (key, mentions) names the merged entity: the fuller name (it carries
    the patronymic), then the more mentioned, then the key."""
    return (len(first[0].split()), first[1], second[0]) >= (
        len(second[0].split()),
        second[1],
        first[0],
    )


def merge_decided(entities: Sequence[Entity], same: Iterable[tuple[str, str]]) -> list[Entity]:
    """A rebuild's entities with every pair decided «same» merged, as `merge_groups`
    merged them when decided. Keys absent from this rebuild are ignored."""
    by_key = {entity.key: entity for entity in entities}
    parent = {key: key for key in by_key}

    def find(key: str) -> str:
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    for first, second in same:
        if first not in by_key or second not in by_key:
            continue
        first, second = find(first), find(second)
        if first == second:
            continue
        a, b = by_key[first], by_key[second]
        keep, drop = (
            (first, second)
            if _keeps_first((first, len(a.mention_ids)), (second, len(b.mention_ids)))
            else (second, first)
        )
        parent[drop] = keep
    merged = {
        key: Entity(
            key=key,
            name=entity.name,
            mention_ids=list(entity.mention_ids),
            variants=Counter(entity.variants),
            gender=entity.gender,
            name_source=entity.name_source,
            patronymic=entity.patronymic,
            regions=Counter(entity.regions),
        )
        for key, entity in by_key.items()
        if find(key) == key
    }
    for key, entity in by_key.items():
        target = merged[find(key)]
        if target.key == key:
            continue
        target.mention_ids += entity.mention_ids
        target.variants.update(entity.variants)
        target.regions.update(entity.regions)
        target.gender = target.gender or entity.gender
    return sorted(merged.values(), key=lambda entity: (-len(entity.mention_ids), entity.key))


def same_pairs(session: Session) -> list[tuple[str, str]]:
    return [
        (record.key_a, record.key_b)
        for record in session.scalars(
            select(EntityPairDecisionRecord).where(EntityPairDecisionRecord.decision == SAME)
        )
    ]


def decided_pairs(session: Session) -> list[tuple[str, str]]:
    return [
        (key_a, key_b)
        for key_a, key_b in session.execute(
            select(EntityPairDecisionRecord.key_a, EntityPairDecisionRecord.key_b)
        ).all()
    ]


def _counts(first: Iterable[list[Any]], second: Iterable[list[Any]]) -> list[list[Any]]:
    total: Counter[str] = Counter()
    for form, count in [*first, *second]:
        total[str(form)] += int(count)
    return [list(item) for item in total.most_common()]


def _sum(first: Mapping[str, int], second: Mapping[str, int]) -> dict[str, int]:
    return dict(Counter(first) + Counter(second))


_PUBLICATIONS = text(
    """
    SELECT count(DISTINCT r.article_id), max(a.published_at)
    FROM entity_group_mentions gm
    JOIN entity_mentions m ON m.id = gm.mention_id
    JOIN article_extraction_runs r ON r.id = m.extraction_run_id
    JOIN parsed_articles a ON a.id = r.article_id
    WHERE gm.group_id = :group
    """
)


def merge_groups(session: Session, first: EntityGroupRecord, second: EntityGroupRecord) -> int:
    """Merge two entities in the caller's transaction; the id of the one kept.

    Its mentions, Criminal Code articles and list matches join the kept one, which
    keeps the stronger role of the two; the other is deleted."""
    keep, drop = (
        (first, second)
        if _keeps_first((first.key, first.mention_count), (second.key, second.mention_count))
        else (second, first)
    )
    for table in (EntityGroupMentionRecord, EntityGroupChargeRecord, EntityGroupRfMatchRecord):
        session.execute(update(table).where(table.group_id == drop.id).values(group_id=keep.id))
    # One list entry matched twice: the stronger level stays.
    session.execute(
        text(
            """
            DELETE FROM entity_group_rf_matches WHERE group_id = :keep AND id NOT IN (
                SELECT DISTINCT ON (entry_id) id FROM entity_group_rf_matches
                WHERE group_id = :keep ORDER BY entry_id, (level = 'full') DESC, id)
            """
        ),
        {"keep": keep.id},
    )
    kept_role = session.get(EntityGroupRoleRecord, keep.id)
    dropped_role = session.get(EntityGroupRoleRecord, drop.id)
    if dropped_role is not None and (
        kept_role is None or _ROLE_RANK[dropped_role.role] > _ROLE_RANK[kept_role.role]
    ):
        if kept_role is not None:
            session.delete(kept_role)
            session.flush()
        session.execute(
            update(EntityGroupRoleRecord)
            .where(EntityGroupRoleRecord.group_id == drop.id)
            .values(group_id=keep.id)
        )
    keep.variants = _counts(keep.variants, drop.variants)
    keep.regions = _counts(keep.regions, drop.regions)
    keep.event_types = _sum(keep.event_types, drop.event_types)
    keep.gender = keep.gender or drop.gender
    session.flush()
    keep.mention_count = (
        session.scalar(select(func.count()).where(EntityGroupMentionRecord.group_id == keep.id))
        or 0
    )
    publications, latest = session.execute(_PUBLICATIONS, {"group": keep.id}).one()
    keep.article_count = int(publications or 0)
    keep.last_published_at = latest
    session.execute(delete(EntityGroupRecord).where(EntityGroupRecord.id == drop.id))
    return keep.id


def decide(session: Session, first_key: str, second_key: str, decision: str) -> int | None:
    """Record a decision in the caller's transaction; merge at once when «same». The id
    of the merged entity, or None."""
    if decision not in (SAME, DIFFERENT):
        raise ValueError(f"unknown decision: {decision}")
    key_a, key_b = pair_keys(first_key, second_key)
    record = session.get(EntityPairDecisionRecord, (key_a, key_b))
    if record is None:
        session.add(EntityPairDecisionRecord(key_a=key_a, key_b=key_b, decision=decision))
    else:
        record.decision = decision
    session.flush()
    if decision != SAME:
        return None
    groups = session.scalars(
        select(EntityGroupRecord).where(EntityGroupRecord.key.in_((key_a, key_b)))
    ).all()
    if len(groups) != 2:
        return None
    return merge_groups(session, groups[0], groups[1])
