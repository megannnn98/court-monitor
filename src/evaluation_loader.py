from pathlib import Path

from pydantic import TypeAdapter

from evaluation_models import EvaluationCase, EvaluationDocument

_DOCUMENTS_ADAPTER = TypeAdapter(list[EvaluationDocument])
_CASES_ADAPTER = TypeAdapter(list[EvaluationCase])


def load_evaluation_documents(path: Path) -> list[EvaluationDocument]:
    return _DOCUMENTS_ADAPTER.validate_json(path.read_bytes())


def load_evaluation_cases(path: Path) -> list[EvaluationCase]:
    return _CASES_ADAPTER.validate_json(path.read_bytes())
