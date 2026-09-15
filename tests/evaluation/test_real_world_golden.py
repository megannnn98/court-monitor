"""Golden dataset schema validation: offsets, references, contradictions, split leakage."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from evaluation.real_world.golden import (
    AnnotationStatus,
    GoldenArticle,
    GoldenDataset,
    GoldenPerson,
    GoldenSplit,
    assign_splits,
    excerpt_windows,
    load_golden_dataset,
    manifest_problems,
    text_problems,
    write_golden_dataset,
)
from evaluation.real_world.models import (
    CorpusManifest,
    ManifestArticle,
    TemporalPeriod,
    sha256_text,
)

TEXT = (
    "В Москве задержали Ивана Петрова и Марию Сидорову. "
    "Суд арестовал Петрова по статье о дискредитации армии. "
    "Сидорову отпустили."
)
PUBLISHED = datetime(2026, 4, 1, 12, tzinfo=UTC)


def span(text: str, occurrence: int = 1, source: str = TEXT) -> dict[str, Any]:
    start = -1
    for _ in range(occurrence):
        start = source.index(text, start + 1)
    return {"start": start, "end": start + len(text), "text": text}


def article_payload(case_id: str = "c1", **overrides: Any) -> dict[str, Any]:
    mentions = [
        {**span("Ивана Петрова"), "golden_person_id": "gp-petrov"},
        {**span("Марию Сидорову"), "golden_person_id": "gp-sidorova"},
    ]
    events: list[dict[str, Any]] = [
        {
            "event_id": "e1",
            "event_type": "detention",
            "person_ids": ["gp-petrov", "gp-sidorova"],
            "shared": True,
            "evidence": span("В Москве задержали Ивана Петрова и Марию Сидорову."),
        }
    ]
    spans = [(m["start"], m["end"]) for m in mentions] + [
        (e["evidence"]["start"], e["evidence"]["end"]) for e in events
    ]
    payload: dict[str, Any] = {
        "case_id": case_id,
        "source": "ovd-info",
        "external_id": f"/express-news/2026/04/01/{case_id}",
        "canonical_url": f"https://ovd.info/express-news/2026/04/01/{case_id}",
        "published_at": PUBLISHED.isoformat(),
        "content_hash": sha256_text(TEXT),
        "split": "dev",
        "excerpts": [s.model_dump() for s in excerpt_windows(TEXT, spans)],
        "mentions": mentions,
        "events": events,
    }
    payload.update(overrides)
    return payload


def persons() -> list[GoldenPerson]:
    return [
        GoldenPerson(golden_person_id="gp-petrov", canonical_name="Иван Петров"),
        GoldenPerson(golden_person_id="gp-sidorova", canonical_name="Мария Сидорова"),
    ]


def dataset(
    articles: list[dict[str, Any]] | None = None, people: list[GoldenPerson] | None = None
) -> GoldenDataset:
    return GoldenDataset.model_validate(
        {
            "version": {
                "dataset_version": "real-world-v1",
                "golden_dataset_hash": "",
                "rf_snapshot_id": "rf-eval-v1",
                "rf_snapshot_path": "evaluation/real_world/rf_snapshot_eval_v1.csv",
            },
            "persons": [p.model_dump() for p in (people or persons())],
            "articles": articles or [article_payload()],
        }
    )


def test_valid_dataset_passes_text_validation() -> None:
    golden = dataset()
    assert text_problems(golden, {golden.articles[0].key: TEXT}) == []


def test_offsets_that_do_not_match_the_text_are_reported() -> None:
    golden = dataset()
    shifted = TEXT.replace("В Москве", "В Питере")
    problems = text_problems(golden, {golden.articles[0].key: shifted})
    assert any("content hash" in p for p in problems)
    assert any("offset" in p for p in problems)


def test_span_length_must_match_its_text() -> None:
    bad = article_payload()
    bad["mentions"][0]["end"] += 1
    with pytest.raises(ValidationError, match="does not have the length"):
        dataset([bad])


def test_mention_outside_every_excerpt_is_rejected() -> None:
    bad = article_payload()
    bad["excerpts"] = [span("Сидорову отпустили.")]
    with pytest.raises(ValidationError, match="not covered by an excerpt"):
        dataset([bad])


def test_duplicate_golden_ids_are_rejected() -> None:
    people = [*persons(), GoldenPerson(golden_person_id="gp-petrov", canonical_name="Другой")]
    with pytest.raises(ValidationError, match="duplicate golden_person_id"):
        dataset(people=people)
    with pytest.raises(ValidationError, match="duplicate case_id"):
        dataset([article_payload(), article_payload(external_id="/express-news/other")])


def test_unknown_person_references_are_rejected() -> None:
    bad = article_payload()
    bad["mentions"][1]["golden_person_id"] = "gp-nobody"
    with pytest.raises(ValidationError, match="unknown gp-nobody"):
        dataset([bad])


def test_same_as_and_distinct_from_contradictions_are_rejected() -> None:
    with pytest.raises(ValidationError, match="both same_as and distinct_from"):
        GoldenPerson(
            golden_person_id="gp-a",
            canonical_name="А",
            same_as=["gp-b"],
            distinct_from=["gp-b"],
        )
    people = [
        GoldenPerson(golden_person_id="gp-petrov", canonical_name="И", same_as=["gp-sidorova"]),
        GoldenPerson(
            golden_person_id="gp-sidorova", canonical_name="М", distinct_from=["gp-petrov"]
        ),
    ]
    with pytest.raises(ValidationError, match="distinct_from it"):
        dataset(people=people)
    with pytest.raises(ValidationError, match="unknown same_as/distinct_from"):
        dataset(
            people=[
                GoldenPerson(golden_person_id="gp-petrov", canonical_name="И", same_as=["gp-x"]),
                persons()[1],
            ]
        )


def test_shared_flag_must_match_the_number_of_persons() -> None:
    bad = article_payload()
    bad["events"][0]["shared"] = False
    with pytest.raises(ValidationError, match="shared must be true"):
        dataset([bad])


def test_candidate_expectation_requires_persecution_and_rf() -> None:
    with pytest.raises(ValidationError, match="needs persecution and RF"):
        GoldenPerson(
            golden_person_id="gp-a",
            canonical_name="А",
            candidate={"expected_main_candidate": True},  # type: ignore[arg-type]
        )
    with pytest.raises(ValidationError, match="POLITICAL and RF NOT_MATCHED"):
        GoldenPerson.model_validate(
            {
                "golden_person_id": "gp-a",
                "canonical_name": "А",
                "persecution": {"expected_status": "political"},
                "rosfinmonitoring": {"snapshot": "rf-eval-v1", "expected_status": "matched"},
                "candidate": {"expected_main_candidate": True},
            }
        )


def test_verified_needs_a_reviewer_and_draft_cannot_have_one() -> None:
    with pytest.raises(ValidationError, match="VERIFIED needs"):
        GoldenArticle.model_validate(article_payload(annotation_status="VERIFIED"))
    with pytest.raises(ValidationError, match="DRAFT cannot"):
        GoldenArticle.model_validate(article_payload(verified_by="someone"))


def test_person_in_two_splits_is_split_leakage() -> None:
    second = article_payload("c2", split="test")
    with pytest.raises(ValidationError, match="split leakage"):
        dataset([article_payload(), second])


def test_duplicate_story_across_splits_is_split_leakage() -> None:
    golden = dataset()
    other = GoldenArticle.model_validate(
        article_payload(
            "c2",
            split="validation",
            mentions=[],
            events=[],
            excerpts=[],
        )
    )
    golden = GoldenDataset.model_construct(
        version=golden.version, persons=golden.persons, articles=[*golden.articles, other]
    )
    manifest = CorpusManifest(
        period_start=date(2026, 3, 1),
        period_end=date(2026, 8, 31),
        sampling_seed=1,
        targets={},
        total_target=2,
        evaluation_sample_size=2,
        built_at=PUBLISHED,
        sources=[],
        articles=[
            ManifestArticle(
                source="ovd-info",
                external_id=article.external_id,
                canonical_url=article.canonical_url,
                published_at=PUBLISHED,
                content_hash=article.content_hash,
                raw_content_hash="x",
                corpus_split=TemporalPeriod.T0,
                duplicate_group="story-1",
            )
            for article in golden.articles
        ],
    )
    assert any("duplicate story story-1" in p for p in manifest_problems(golden, manifest))


def test_select_keeps_only_verified_articles_for_locked_test() -> None:
    golden = dataset()
    assert golden.select(GoldenSplit.DEV, verified_only=True).articles == []
    assert len(golden.select(GoldenSplit.DEV, verified_only=False).articles) == 1
    assert golden.articles[0].annotation_status is AnnotationStatus.DRAFT
    assert not golden.person_verified("gp-petrov")

    locked = dataset([article_payload(split="test")])
    # A DRAFT locked-test case is never evaluated, not even with --split all.
    assert locked.select(GoldenSplit.TEST, verified_only=False).articles == []
    assert locked.select(None, verified_only=False).articles == []


def test_assign_splits_is_deterministic_keeps_groups_and_follows_shares() -> None:
    groups = {f"a{index:03d}": f"g{index // 2}" for index in range(100)}
    split = assign_splits(groups, seed=1)

    assert split == assign_splits(dict(reversed(list(groups.items()))), seed=1)
    for index in range(0, 100, 2):
        assert split[f"a{index:03d}"] == split[f"a{index + 1:03d}"]
    counts = {name: list(split.values()).count(name) for name in GoldenSplit}
    assert counts == {GoldenSplit.DEV: 60, GoldenSplit.VALIDATION: 20, GoldenSplit.TEST: 20}


def test_write_and_load_roundtrip_records_the_hash(tmp_path: Path) -> None:
    golden = dataset()
    write_golden_dataset(golden, tmp_path)
    loaded = load_golden_dataset(tmp_path)
    assert loaded.content_hash() == golden.content_hash()
    assert loaded.version.golden_dataset_hash == golden.content_hash()


def test_hash_changes_with_any_annotation_change() -> None:
    golden = dataset()
    changed = dataset([article_payload(tags=["multi_person"])])
    assert golden.content_hash() != changed.content_hash()
