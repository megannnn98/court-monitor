from pathlib import Path

from evaluation_loader import (
    load_evaluation_cases,
    load_evaluation_documents,
)

FIXTURES_PATH = Path(__file__).parent / "fixtures"
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


def test_expected_chunks_exist_in_corpus() -> None:
    documents = load_evaluation_documents(CORPUS_PATH)
    cases = load_evaluation_cases(CASES_PATH)

    existing_chunks = {
        (
            document.source_base_url,
            document.external_id,
            ordinal,
        )
        for document in documents
        for ordinal, _ in enumerate(document.chunks)
    }

    expected_chunks = {
        (
            case.expected_chunk.source_base_url,
            case.expected_chunk.external_id,
            case.expected_chunk.ordinal,
        )
        for case in cases
    }

    assert expected_chunks <= existing_chunks
