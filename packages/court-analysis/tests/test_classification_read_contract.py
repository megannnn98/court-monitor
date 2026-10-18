"""A stored classifier result leaves its service as a value, not a mapped record."""

from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from analysis.persecution.classification_service import PersecutionClassificationService
from analysis.read import PersonClassification


@pytest.fixture
def reader() -> Iterator[PersecutionClassificationService]:
    engine = create_engine("sqlite://")
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE TABLE persecution_classifications ("
                    "id INTEGER PRIMARY KEY, person_id INTEGER, status TEXT, confidence FLOAT, "
                    "reasons JSON, evidence_types JSON, classifier_name TEXT, "
                    "classifier_version TEXT, classified_at DATETIME, created_at DATETIME)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO persecution_classifications VALUES "
                    "(:id, 7, 'political', 0.8, '[\"reason\"]', '[\"political_article\"]', "
                    ":classifier, :version, :at, :at)"
                ),
                [
                    {"id": 1, "classifier": "first", "version": "1", "at": "2026-10-18 12:00:00"},
                    {"id": 2, "classifier": "second", "version": "1", "at": "2026-10-19 12:00:00"},
                    {"id": 3, "classifier": "first", "version": "0", "at": "2026-10-17 12:00:00"},
                ],
            )
        yield PersecutionClassificationService(sessionmaker(engine, class_=Session))
    finally:
        engine.dispose()


@pytest.mark.parametrize(("classifier", "expected"), [(None, 2), ("", 2), ("first", 1)])
def test_classification_is_a_detached_value_with_the_original_time_order(
    reader: PersecutionClassificationService, classifier: str | None, expected: int
) -> None:
    result = reader.get_person_classification(7, classifier)
    assert isinstance(result, PersonClassification)
    assert inspect(result, raiseerr=False) is None
    assert result.id == expected
    assert result.classified_at is not None
    assert result.classified_at.isoformat() == f"2026-10-{19 if expected == 2 else 18}T12:00:00"
    assert result.evidence_types == ["political_article"]
    result.reasons.append("not persistent")
    reread = reader.get_person_classification(7, classifier)
    assert reread is not None and reread.reasons == ["reason"]


def test_missing_classifier_or_person_returns_none(
    reader: PersecutionClassificationService,
) -> None:
    assert reader.get_person_classification(7, "missing") is None
    assert reader.get_person_classification(999) is None
