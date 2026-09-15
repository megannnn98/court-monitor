"""Deterministic selection, temporal split, stratified sampling, duplicate groups."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from evaluation.real_world.models import TemporalPeriod
from evaluation.real_world.sampling import (
    DatedKey,
    duplicate_groups,
    seeded_choice,
    select_corpus,
    stratified_sample,
    temporal_split,
)

START = datetime(2026, 3, 1, tzinfo=UTC)


def _dated(count: int) -> list[DatedKey]:
    return [
        DatedKey(key=f"a{index:03d}", published_at=START + timedelta(hours=index))
        for index in range(count)
    ]


def test_temporal_split_follows_publication_time_not_input_order() -> None:
    articles = _dated(100)
    split = temporal_split(list(reversed(articles)))

    periods = [split[article.key] for article in articles]
    assert periods.count(TemporalPeriod.T0) == 70
    assert periods.count(TemporalPeriod.T1) == 10
    assert periods.count(TemporalPeriod.T2) == 10
    assert periods.count(TemporalPeriod.T3) == 10
    # Monotone: no later article lands in an earlier period.
    order = list(TemporalPeriod)
    assert [order.index(period) for period in periods] == sorted(order.index(p) for p in periods)


def test_temporal_split_of_empty_and_tiny_corpora() -> None:
    assert temporal_split([]) == {}
    assert set(temporal_split(_dated(1)).values()) <= set(TemporalPeriod)


def test_seeded_choice_is_deterministic_and_order_independent() -> None:
    keys = [f"k{index}" for index in range(50)]
    first = seeded_choice(keys, 10, seed=7)
    assert first == seeded_choice(list(reversed(keys)), 10, seed=7)
    assert first != seeded_choice(keys, 10, seed=8)
    assert seeded_choice(keys, 99, seed=7) == sorted(keys)


def test_select_corpus_redistributes_quota_a_source_cannot_fill() -> None:
    available = {
        "ovd-info": [f"o{index}" for index in range(900)],
        "sota-vision": [f"s{index}" for index in range(12)],
    }
    selected = select_corpus(
        available, {"ovd-info": 600, "sota-vision": 400}, total_target=1000, seed=1
    )

    assert len(selected["sota-vision"]) == 12
    assert len(selected["ovd-info"]) == 900  # 600 + all 388 would exceed what exists
    again = select_corpus(available, {"ovd-info": 600, "sota-vision": 400}, 1000, seed=1)
    assert again == selected


def test_select_corpus_respects_the_total_target() -> None:
    available = {"a": [f"a{i}" for i in range(2000)], "b": [f"b{i}" for i in range(10)]}
    selected = select_corpus(available, {"a": 600, "b": 400}, total_target=1000, seed=3)
    assert len(selected["a"]) + len(selected["b"]) == 1000


def test_stratified_sample_covers_rare_strata_and_is_deterministic() -> None:
    tags = {f"k{index:03d}": ["single_person"] for index in range(300)}
    tags["k500"] = ["hyphenated_name"]
    tags["k501"] = ["yo_letter"]

    sample = stratified_sample(tags, size=20, seed=5)

    assert len(sample) == 20
    assert {"k500", "k501"} <= set(sample)
    assert sample == stratified_sample(dict(reversed(list(tags.items()))), size=20, seed=5)


def test_stratified_sample_never_exceeds_the_corpus() -> None:
    assert stratified_sample({"a": [], "b": ["initials"]}, size=10, seed=1) == ["a", "b"]


def test_duplicate_groups_join_near_copies_only() -> None:
    story = " ".join(f"слово{index}" for index in range(60))
    texts = {
        "sota:x": story,
        "ovd:y": story + " дополнение",
        "ovd:z": " ".join(f"другое{index}" for index in range(60)),
    }
    groups = duplicate_groups(texts)
    assert groups["sota:x"] == groups["ovd:y"] == "ovd:y"
    assert groups["ovd:z"] == "ovd:z"
