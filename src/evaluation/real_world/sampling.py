"""Deterministic corpus selection, temporal split, stratified sample, duplicate groups.

Every function is pure: the same inputs and seed give the same result, so the
manifest can be rebuilt and compared.
"""

from __future__ import annotations

import random
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from evaluation.real_world.models import TemporalPeriod

# Cumulative upper bounds of T0..T3 as fractions of the time-ordered corpus.
TEMPORAL_BOUNDS = ((TemporalPeriod.T0, 0.7), (TemporalPeriod.T1, 0.8), (TemporalPeriod.T2, 0.9))

# Stratum order for the evaluation sample: rarest, most informative cases first.
SAMPLING_STRATA = (
    "hyphenated_name",
    "yo_letter",
    "initials",
    "multi_person",
    "several_events",
    "shared_sentence",
    "historical_reference",
    "political_keywords",
    "non_political",
    "single_person",
)

DUPLICATE_SHINGLE_SIZE = 5
DUPLICATE_MIN_JACCARD = 0.7


@dataclass(frozen=True)
class DatedKey:
    key: str
    published_at: datetime


def temporal_split(articles: Sequence[DatedKey]) -> dict[str, TemporalPeriod]:
    """First 70% by publication time T0, then 10% each T1, T2, T3."""
    ordered = sorted(articles, key=lambda article: (article.published_at, article.key))
    count = len(ordered)
    bounds = [(period, round(count * fraction)) for period, fraction in TEMPORAL_BOUNDS]
    split: dict[str, TemporalPeriod] = {}
    for index, article in enumerate(ordered):
        split[article.key] = next(
            (period for period, bound in bounds if index < bound), TemporalPeriod.T3
        )
    return split


def seeded_choice(keys: Sequence[str], count: int, seed: int) -> list[str]:
    """`count` keys chosen by `seed`, independent of input order; sorted."""
    ordered = sorted(keys)
    if count >= len(ordered):
        return ordered
    return sorted(random.Random(seed).sample(ordered, count))


def select_corpus(
    available: Mapping[str, Sequence[str]],
    targets: Mapping[str, int],
    total_target: int,
    seed: int,
) -> dict[str, list[str]]:
    """Per-source selection; quota a source cannot fill goes to the others.

    Sources are visited in name order so redistribution is deterministic.
    """
    selected: dict[str, list[str]] = {}
    remaining = total_target
    shortfall = 0
    names = sorted(available)
    for name in names:
        quota = targets.get(name, 0)
        take = min(quota, len(available[name]))
        shortfall += quota - take
        selected[name] = seeded_choice(available[name], take, seed)
        remaining -= take
    extra = max(0, min(shortfall, remaining))
    for name in names:
        if extra == 0:
            break
        left = sorted(set(available[name]) - set(selected[name]))
        take = min(extra, len(left))
        selected[name] = sorted(selected[name] + seeded_choice(left, take, seed))
        extra -= take
    return selected


def stratified_sample(
    tags: Mapping[str, Sequence[str]],
    size: int,
    seed: int,
    strata: Sequence[str] = SAMPLING_STRATA,
) -> list[str]:
    """Round-robin over strata (seeded order inside each), then fill from the rest."""
    rng = random.Random(seed)
    queues: list[list[str]] = []
    for stratum in strata:
        members = sorted(key for key, key_tags in tags.items() if stratum in key_tags)
        rng.shuffle(members)
        queues.append(members)
    rest = sorted(tags)
    rng.shuffle(rest)
    queues.append(rest)

    chosen: list[str] = []
    seen: set[str] = set()
    target = min(size, len(tags))
    while len(chosen) < target and any(queues):
        for queue in queues:
            while queue:
                key = queue.pop(0)
                if key not in seen:
                    seen.add(key)
                    chosen.append(key)
                    break
            if len(chosen) == target:
                break
    return sorted(chosen)


_WORD = re.compile(r"\w+", re.UNICODE)


def _shingles(text: str) -> set[tuple[str, ...]]:
    words = [word.lower().replace("ё", "е") for word in _WORD.findall(text)]
    if len(words) < DUPLICATE_SHINGLE_SIZE:
        return {tuple(words)} if words else set()
    return {
        tuple(words[index : index + DUPLICATE_SHINGLE_SIZE])
        for index in range(len(words) - DUPLICATE_SHINGLE_SIZE + 1)
    }


def duplicate_groups(texts: Mapping[str, str]) -> dict[str, str]:
    """Group near-copies (word 5-gram Jaccard >= 0.7); group id = smallest member key."""
    keys = sorted(texts)
    shingles = {key: _shingles(texts[key]) for key in keys}
    parent = {key: key for key in keys}

    def find(key: str) -> str:
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    for index, first in enumerate(keys):
        first_set = shingles[first]
        if not first_set:
            continue
        for second in keys[index + 1 :]:
            second_set = shingles[second]
            if not second_set:
                continue
            smaller, larger = sorted((len(first_set), len(second_set)))
            if smaller / larger < DUPLICATE_MIN_JACCARD:
                continue
            union = len(first_set | second_set)
            if len(first_set & second_set) / union >= DUPLICATE_MIN_JACCARD:
                root_first, root_second = find(first), find(second)
                if root_first != root_second:
                    parent[max(root_first, root_second)] = min(root_first, root_second)
    return {key: find(key) for key in keys}
