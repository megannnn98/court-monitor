"""The final evaluation corpus is complete and internally consistent (no database)."""

from __future__ import annotations

from evaluation.final.corpus import FINAL_EVALUATION_DATASET_VERSION, load_final_corpus

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
