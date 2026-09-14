from pathlib import Path

from search.evaluation_loader import (
    load_evaluation_cases,
    load_evaluation_documents,
)

FIXTURES_PATH = Path(__file__).parents[1] / "fixtures"
CORPUS_PATH = FIXTURES_PATH / "evaluation_corpus.json"
CASES_PATH = FIXTURES_PATH / "evaluation_cases.json"


def test_evaluation_corpus_is_valid() -> None:
    documents = load_evaluation_documents(CORPUS_PATH)

    assert len(documents) == 6
    assert len({document.external_id for document in documents}) == 6


def test_evaluation_cases_are_valid() -> None:
    cases = load_evaluation_cases(CASES_PATH)

    assert len(cases) == 4
    assert len({case.query_id for case in cases}) == len(cases)


def test_expected_articles_exist_in_corpus() -> None:
    documents = load_evaluation_documents(CORPUS_PATH)
    cases = load_evaluation_cases(CASES_PATH)

    existing_articles = {(document.source_base_url, document.external_id) for document in documents}

    expected_articles = {
        (case.expected_article.source_base_url, case.expected_article.external_id) for case in cases
    }

    assert expected_articles <= existing_articles
