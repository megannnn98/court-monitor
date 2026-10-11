"""Entity grouping as `EntityCollector.run` does it, with the rules alone."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from entities.grouping import (
    Entity,
    PersonMention,
    apply_names,
    attach_bare,
    group_mentions,
    kinship,
    merge_swapped,
)


def rules_only_entities(
    mentions: Sequence[PersonMention], article_of: Mapping[int, int]
) -> list[Entity]:
    """The steps of `EntityCollector.run` in its order, minus what is not a rule: the
    model's names (`apply_names` gets none), a person's pair decisions and name
    corrections, and the pseudonyms in brackets, which need the article's text."""
    entities = group_mentions([mention for mention in mentions if not kinship(mention)])
    entities = merge_swapped(apply_names(entities, {}))
    return attach_bare(entities, article_of)


def entity_of_mention(entities: Sequence[Entity]) -> dict[int, str]:
    return {mention_id: entity.key for entity in entities for mention_id in entity.mention_ids}
