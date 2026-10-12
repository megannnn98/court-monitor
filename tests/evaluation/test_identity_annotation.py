"""The annotation artifact of the identity comparison: its shape, the text it shows,
its ids, and that the database it reads cannot be written. Every name here is made up.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

from evaluation.identity_comparison import annotation, real
from evaluation.identity_comparison.annotation import MentionContext

ARTICLE = "Суд в Энске арестовал Ивана Сидорова. Сидорову грозит срок. " * 20


def _context(mention_id: int, article_id: int, surface: str = "Ивана Сидорова") -> MentionContext:
    start = ARTICLE.index(surface, 400)
    return MentionContext(
        mention_id=mention_id,
        surface=surface,
        normalized="Иван Сидоров",
        normalized_data={"first_name": "Иван", "last_name": "Сидоров"},
        start=start,
        end=start + len(surface),
        person_id=None,
        article_id=article_id,
        title="Арест в Энске",
        published_at="2026-01-02T00:00:00+00:00",
        article_text=ARTICLE,
        source="Вестник Энска",
        url="https://example.test/1",
        events=({"event_type": "arrest", "role": "target", "event_date": None},),
        er_decision={"action": "review", "status": "pending_review"},
    )


CONTEXTS = {
    mention_id: _context(mention_id, article_id=mention_id // 10) for mention_id in range(10, 80)
}
SAMPLE: list[dict[str, Any]] = [
    {
        "stratum": "rules_join_er_splits/other",
        "entity": "иван сидоров",
        "persons": [
            {"person_id": 1, "canonical_name": "Иван Сидоров", "mention_ids": [10, 11, 20, 30, 40]},
            {"person_id": 2, "canonical_name": "Сидоров Иван Ильич", "mention_ids": [50]},
            {"person_id": 3, "canonical_name": "Иван Сидорова", "mention_ids": [60]},
        ],
    },
    {
        "stratum": "er_joins_rules_split",
        "person_id": 4,
        "canonical_name": "Анна Петрова",
        "entities": [
            {"entity": "анна петров", "mention_ids": [12]},
            {"entity": "анна петрова город", "mention_ids": [22]},
        ],
    },
    {
        "stratum": "er_review_rules_place",
        "mention_id": 13,
        "entity": "иван сидоров",
        "other_mention_ids": [23, 33],
    },
    {
        "stratum": "er_review_rules_place",
        "mention_id": 14,
        "entity": "петр иванов",
        "other_mention_ids": [],
    },
]


def test_a_snippet_is_a_window_around_the_mention_cut_at_spaces() -> None:
    context = CONTEXTS[10]

    found = annotation.snippet(context.article_text, context.start, context.end, window=50)

    assert found["text"][found["mention_start"] : found["mention_end"]] == context.surface
    assert len(found["text"]) <= len(context.surface) + 100
    assert found["cut_before"] and found["cut_after"]
    assert not found["text"].startswith(" ") and not found["text"].endswith(" ")
    # Whole words only: the window does not end inside one.
    assert f" {found['text']} " in f" {context.article_text} "


def test_a_snippet_of_a_short_text_is_the_whole_text() -> None:
    found = annotation.snippet("Арестован Иван Сидоров.", 10, 22)

    assert found == {
        "text": "Арестован Иван Сидоров.",
        "mention_start": 10,
        "mention_end": 22,
        "cut_before": False,
        "cut_after": False,
    }


def test_case_ids_depend_on_the_mentions_and_the_kind_only() -> None:
    first = annotation.case_id("er_joins_rules_split", [3, 1, 2])

    assert first == annotation.case_id("er_joins_rules_split", [1, 2, 3])
    assert first != annotation.case_id("er_joins_rules_split", [1, 2, 4])
    assert first != annotation.case_id("er_review_rules_place", [1, 2, 3])
    assert first.startswith("case-") and len(first) == len("case-") + 12


def test_a_side_is_shown_by_a_few_mentions_of_different_articles() -> None:
    assert annotation.shown_mentions([10, 11, 20, 30, 40, 999], CONTEXTS) == [10, 20, 30]
    assert annotation.shown_mentions([10, 11], CONTEXTS) == [10, 11]


def test_every_case_becomes_pairs_of_sides_with_an_empty_label() -> None:
    cases = annotation.annotate(SAMPLE, CONTEXTS, {10: "иван сидоров"})

    three, two, review, alone = cases
    assert [len(case["sides"]) for case in cases] == [3, 2, 2, 2]
    assert [pair["sides"] for pair in three["pairs"]] == [["a", "b"], ["a", "c"], ["b", "c"]]
    assert [pair["sides"] for pair in two["pairs"]] == [["a", "b"]]
    assert review["decisions"] == {"er_v2": "left_to_a_human", "rules": "one_entity"}
    assert review["sides"][0]["kind"] == "mention" and review["sides"][1]["kind"] == "rules_entity"
    # Nothing was joined to a mention that is alone in its entity: nothing to ask.
    assert alone["pairs"] == [] and alone["not_ready"] is not None
    assert all(case["not_ready"] is None for case in (three, two, review))

    pairs = [pair for case in cases for pair in case["pairs"]]
    assert all(pair["annotation"] == {"label": None, "comment": None} for pair in pairs)
    assert len({pair["pair_id"] for pair in pairs}) == len(pairs)
    assert len({case["case_id"] for case in cases}) == len(cases)

    mention = three["sides"][0]["mentions"][0]
    assert set(mention) == {
        "mention_id",
        "article_id",
        "surface",
        "normalized",
        "normalized_data",
        "er_person_id",
        "er_decision",
        "rules_entity",
        "article",
        "events",
        "context",
    }
    assert mention["rules_entity"] == "иван сидоров"
    assert set(mention["article"]) == {"title", "published_at", "source", "url"}
    assert three["sides"][0]["mentions_in_group"] == 5 and len(three["sides"][0]["mentions"]) == 3


def test_the_same_sample_gives_the_same_artifact() -> None:
    assert annotation.annotate(SAMPLE, CONTEXTS, {}) == annotation.annotate(SAMPLE, CONTEXTS, {})


def test_the_summary_counts_cases_pairs_and_text() -> None:
    summary = annotation.summary(annotation.annotate(SAMPLE, CONTEXTS, {}))

    assert (summary["cases"], summary["cases_ready"], summary["pairs_to_label"]) == (4, 3, 5)
    assert sum(summary["cases_not_ready"].values()) == 1
    assert 0 < summary["context_chars_per_mention_mean"] <= summary["context_chars_per_mention_max"]


def test_an_unknown_kind_of_case_is_refused() -> None:
    with pytest.raises(ValueError, match="unknown stratum"):
        annotation.sides_of({"stratum": "something_else"})


def test_the_database_that_is_read_cannot_be_written(test_engine: Engine) -> None:
    engine = real.read_only_engine(test_engine.url.render_as_string(hide_password=False))
    try:
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM sources")) is not None
            with pytest.raises(DBAPIError, match="read-only transaction"):
                connection.execute(text("CREATE TABLE identity_comparison_probe (id int)"))
        with (
            engine.connect() as connection,
            pytest.raises(DBAPIError, match="read-only transaction"),
        ):
            connection.execute(text("INSERT INTO sources (name, base_url) VALUES ('x', 'y')"))
    finally:
        engine.dispose()
