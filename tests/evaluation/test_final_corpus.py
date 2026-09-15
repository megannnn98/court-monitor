"""The final evaluation corpus is complete and internally consistent (no database)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from evaluation.final.corpus import (
    DANGEROUS_KINDS,
    FINAL_EVALUATION_DATASET_VERSION,
    FinalCase,
    is_gated,
    load_final_corpus,
)

REQUIRED_CATEGORIES = {
    "clear_positive",
    "clear_negative",
    "ambiguous_persecution",
    "person_namesakes",
    "swapped_fio",
    "initials",
    "female_surnames",
    "multiple_persons",
    "same_person_several_articles",
    "person_several_events",
    "rf_matched",
    "rf_not_matched",
    "rf_ambiguous",
    "rf_insufficient_data",
    "no_rf_snapshot",
    "er_review",
    "semantic_only_query",
    "lexically_obvious_query",
    "irrelevant_semantic_query",
    "duplicate_source_article",
    "repeated_monitoring_run",
    "new_article_second_run",
}


def test_corpus_covers_every_required_category_with_enough_cases() -> None:
    corpus = load_final_corpus()

    assert corpus.dataset_version == FINAL_EVALUATION_DATASET_VERSION
    assert len(corpus.cases) >= 40
    categories = {category for case in corpus.cases for category in case.categories}
    assert REQUIRED_CATEGORIES <= categories


def test_every_case_checks_something() -> None:
    for case in load_final_corpus().cases:
        checks = (
            case.expected_extraction
            or case.identities
            or case.research
            or case.run_expectations
            or case.review_required is not None
        )
        assert checks, case.id


def test_known_limitations_are_explained() -> None:
    for case in load_final_corpus().cases:
        if case.known_limitation is not None:
            assert len(case.known_limitation) > 20, case.id


def _case(**overrides: object) -> dict[str, object]:
    return {
        "id": "case",
        "categories": ["clear_positive"],
        "description": "synthetic case",
        "articles": [{"external_id": "a", "title": "t", "text": "text"}],
        "review_required": False,
        **overrides,
    }


def test_known_limitation_excuses_only_the_declared_dangerous_kinds() -> None:
    """A documented ER limitation must not also hide a false «absent from the RF list»."""
    case = FinalCase.model_validate(
        _case(
            known_limitation="ER has no context features: namesakes are auto-linked.",
            known_limitation_kinds=["false_person_link"],
        )
    )

    assert is_gated(case, "false_person_link") is False
    assert is_gated(case, "false_rf_not_matched") is True
    assert is_gated(FinalCase.model_validate(_case()), "false_person_link") is True


def test_known_limitation_without_declared_kinds_excuses_nothing() -> None:
    case = FinalCase.model_validate(
        _case(known_limitation="Surname-only mentions are not extracted at all.")
    )

    for kind in DANGEROUS_KINDS:
        assert is_gated(case, kind) is True


@pytest.mark.parametrize(
    "overrides",
    [
        {"known_limitation_kinds": ["false_person_link"]},
        {"known_limitation": "A documented limitation text.", "known_limitation_kinds": ["typo"]},
    ],
)
def test_excused_kinds_need_a_limitation_and_a_known_kind(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        FinalCase.model_validate(_case(**overrides))
