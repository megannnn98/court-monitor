"""Pure mapping from stored records to research result models.

No queries here: the repository loads records, these functions decide how
they are represented and which of them need human review.
"""

from __future__ import annotations

from collections.abc import Iterable

from candidates.models import RosfinmonitoringStatus, resolve_rosfinmonitoring_status
from db.orm_models import (
    EntityMentionRecord,
    ExtractedEventRecord,
    PersecutionClassificationRecord,
    PersonAliasRecord,
    PersonRecord,
    RosfinMatchRecord,
)
from extraction.models import EventEntityRole, EventType
from persecution.models import (
    PersecutionClassification,
    PersecutionClassificationStatus,
    PersecutionEvidenceType,
)
from persons.models import AliasOrigin, Person, PersonAlias, PersonStatus
from research.models import (
    ResearchEvent,
    ResearchEvidence,
    ResearchEvidenceType,
    ResearchRosfinmonitoring,
    ResearchWarning,
    ResearchWarningCode,
)
from rosfinmonitoring.matcher_models import RosfinCandidateEntry

_PERSECUTION_REVIEW_WARNINGS: dict[
    PersecutionClassificationStatus, tuple[ResearchWarningCode, str]
] = {
    PersecutionClassificationStatus.UNCERTAIN: (
        ResearchWarningCode.PERSECUTION_UNCERTAIN,
        (
            "Persecution classification is uncertain; a human must decide whether "
            "the persecution is political."
        ),
    ),
    PersecutionClassificationStatus.NEEDS_REVIEW: (
        ResearchWarningCode.PERSECUTION_NEEDS_REVIEW,
        "Persecution classification is marked as needing review.",
    ),
}

_ROSFIN_WARNINGS: dict[RosfinmonitoringStatus, tuple[ResearchWarningCode, str, bool]] = {
    RosfinmonitoringStatus.AMBIGUOUS: (
        ResearchWarningCode.ROSFIN_AMBIGUOUS,
        (
            "Several Rosfinmonitoring entries match this person; not a confirmed "
            "presence or absence."
        ),
        True,
    ),
    RosfinmonitoringStatus.NEEDS_REVIEW: (
        ResearchWarningCode.ROSFIN_NEEDS_REVIEW,
        "Rosfinmonitoring match needs human review; not a confirmed presence or absence.",
        True,
    ),
    RosfinmonitoringStatus.INSUFFICIENT_DATA: (
        ResearchWarningCode.ROSFIN_INSUFFICIENT_DATA,
        (
            "Person name data is too thin to search the snapshot reliably; finding "
            "nothing does not mean the person is absent."
        ),
        True,
    ),
    RosfinmonitoringStatus.NO_MATCH_RECORD: (
        ResearchWarningCode.ROSFIN_NO_MATCH_RECORD,
        (
            "Rosfinmonitoring matching has not been run for this person against "
            "this snapshot; absence is not confirmed."
        ),
        False,
    ),
}


def build_warnings(
    persecution: PersecutionClassification | None,
    rosfinmonitoring: ResearchRosfinmonitoring | None,
    *,
    evidence_truncated: bool = False,
) -> list[ResearchWarning]:
    """Explain why a result is not a confirmed answer.

    `rosfinmonitoring` is None when the request named no snapshot, which is
    not a data gap and produces no warning.
    """
    warnings: list[ResearchWarning] = []

    if persecution is None:
        warnings.append(
            ResearchWarning(
                code=ResearchWarningCode.PERSECUTION_NOT_CLASSIFIED,
                message="Person has no persecution classification yet.",
                requires_review=False,
            )
        )
    elif persecution.status in _PERSECUTION_REVIEW_WARNINGS:
        code, message = _PERSECUTION_REVIEW_WARNINGS[persecution.status]
        warnings.append(ResearchWarning(code=code, message=message, requires_review=True))

    if rosfinmonitoring is not None and rosfinmonitoring.status in _ROSFIN_WARNINGS:
        code, message, requires_review = _ROSFIN_WARNINGS[rosfinmonitoring.status]
        warnings.append(
            ResearchWarning(code=code, message=message, requires_review=requires_review)
        )

    if evidence_truncated:
        warnings.append(
            ResearchWarning(
                code=ResearchWarningCode.EVIDENCE_TRUNCATED,
                message="Only the newest mentions and events of this person are included.",
                requires_review=False,
            )
        )

    return warnings


def person_from_record(record: PersonRecord) -> Person:
    return Person(
        id=record.id,
        canonical_name=record.canonical_name,
        normalized_name=record.normalized_name,
        matching_key=record.matching_key,
        status=PersonStatus(record.status),
        merged_into_id=record.merged_into_id,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def alias_from_record(record: PersonAliasRecord) -> PersonAlias:
    return PersonAlias(
        id=record.id,
        person_id=record.person_id,
        surface_text=record.surface_text,
        normalized_text=record.normalized_text,
        matching_key=record.matching_key,
        origin=AliasOrigin(record.origin),
        confidence=record.confidence,
        source_mention_id=record.source_mention_id,
        created_at=record.created_at,
    )


def classification_from_record(
    record: PersecutionClassificationRecord,
) -> PersecutionClassification:
    return PersecutionClassification(
        id=record.id,
        person_id=record.person_id,
        status=PersecutionClassificationStatus(record.status),
        confidence=record.confidence,
        reasons=list(record.reasons),
        evidence_types=[PersecutionEvidenceType(value) for value in record.evidence_types],
        classifier_name=record.classifier_name,
        classifier_version=record.classifier_version,
        classified_at=record.classified_at,
    )


def rosfinmonitoring_from_match(
    record: RosfinMatchRecord | None,
    *,
    snapshot_id: int,
) -> ResearchRosfinmonitoring:
    status = resolve_rosfinmonitoring_status(None if record is None else record.status)
    if record is None:
        return ResearchRosfinmonitoring(snapshot_id=snapshot_id, status=status)
    return ResearchRosfinmonitoring(
        snapshot_id=snapshot_id,
        status=status,
        confidence=record.confidence,
        matched_entry_id=record.matched_entry_id,
        matched_entry_name=record.matched_entry_name,
        candidate_entries=[
            RosfinCandidateEntry.model_validate(entry) for entry in record.candidate_entries
        ],
        reasons=list(record.reasons),
        matched_at=record.matched_at,
    )


def mention_evidence(mention: EntityMentionRecord, *, article_id: int) -> ResearchEvidence:
    return ResearchEvidence(
        evidence_type=ResearchEvidenceType.PERSON_MENTION,
        article_id=article_id,
        extraction_run_id=mention.extraction_run_id,
        start_offset=mention.start_offset,
        end_offset=mention.end_offset,
        text=mention.surface_text,
        mention_id=mention.id,
    )


def event_evidence(
    event: ExtractedEventRecord,
    *,
    article_id: int,
    span_text: str,
) -> ResearchEvidence:
    return ResearchEvidence(
        evidence_type=ResearchEvidenceType.EVENT,
        article_id=article_id,
        extraction_run_id=event.extraction_run_id,
        start_offset=event.start_offset,
        end_offset=event.end_offset,
        text=span_text,
        event_id=event.id,
    )


def research_event(
    event: ExtractedEventRecord,
    *,
    roles: Iterable[EventEntityRole],
    article_id: int,
) -> ResearchEvent:
    return ResearchEvent(
        event_id=event.id,
        event_type=EventType(event.event_type),
        event_date=event.event_date,
        roles=sorted(set(roles)),
        confidence=event.confidence,
        attributes=dict(event.attributes),
        article_id=article_id,
    )
