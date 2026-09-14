import pytest
from pydantic import ValidationError

from persons.models import (
    AliasOrigin,
    MergeRecord,
    MergeStatus,
    Person,
    PersonAlias,
    PersonStatus,
    ResolutionContext,
    ResolutionResult,
    ResolutionStatus,
    ReviewDecision,
    ReviewRecord,
)


def test_person_requires_canonical_and_matching_key() -> None:
    person = Person(
        canonical_name="Иван Иванов",
        normalized_name="Иван Иванов",
        matching_key="иваниванов",
    )

    assert person.status is PersonStatus.ACTIVE
    assert person.merged_into_id is None


def test_person_rejects_empty_name() -> None:
    with pytest.raises(ValidationError):
        Person(canonical_name="", normalized_name="", matching_key="")


def test_person_alias_requires_person_id_and_origin() -> None:
    alias = PersonAlias(
        person_id=1,
        surface_text="Ивана Иванова",
        normalized_text="Иван Иванов",
        matching_key="иваниванов",
        origin=AliasOrigin.EXTRACTION,
        confidence=0.9,
    )

    assert alias.person_id == 1
    assert alias.origin is AliasOrigin.EXTRACTION


def test_person_alias_rejects_invalid_confidence() -> None:
    with pytest.raises(ValidationError):
        PersonAlias(
            person_id=1,
            surface_text="Иван",
            normalized_text="Иван",
            matching_key="иван",
            origin=AliasOrigin.EXTRACTION,
            confidence=1.5,
        )


def test_resolution_result_matched() -> None:
    result = ResolutionResult(
        person_id=42,
        status=ResolutionStatus.MATCHED,
        confidence=0.95,
        reasons=["same matching key", "same city"],
    )

    assert result.status is ResolutionStatus.MATCHED
    assert result.person_id == 42


def test_resolution_result_ambiguous_has_no_person_id() -> None:
    result = ResolutionResult(
        person_id=None,
        status=ResolutionStatus.AMBIGUOUS,
        confidence=0.4,
        reasons=["multiple candidates"],
        candidate_person_ids=[1, 2, 3],
    )

    assert result.status is ResolutionStatus.AMBIGUOUS
    assert result.person_id is None
    assert len(result.candidate_person_ids) == 3


def test_resolution_context_optional_fields() -> None:
    context = ResolutionContext()
    assert context.article_id is None
    assert context.city is None


def test_resolution_context_with_all_fields() -> None:
    context = ResolutionContext(
        article_id=123,
        city="Москва",
        court="Басманный суд",
        organization="ОВД-Инфо",
        event_types=["detention", "arrest"],
    )

    assert context.city == "Москва"
    assert len(context.event_types) == 2


def test_merge_record_tracks_source_and_target() -> None:
    record = MergeRecord(
        source_person_id=1,
        target_person_id=2,
        status=MergeStatus.APPLIED,
        reason="same person different spelling",
    )

    assert record.source_person_id == 1
    assert record.target_person_id == 2
    assert record.status is MergeStatus.APPLIED


def test_review_record_tracks_decision() -> None:
    record = ReviewRecord(
        subject_type="person_resolution",
        subject_id=42,
        decision=ReviewDecision.APPROVED,
        confidence=0.85,
        reason="confirmed same person",
    )

    assert record.decision is ReviewDecision.APPROVED
    assert record.subject_type == "person_resolution"
