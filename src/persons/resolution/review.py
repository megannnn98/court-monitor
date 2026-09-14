"""Human review of ER v2 decisions (reuses `review_records`).

A reviewer sees the stored snapshot of what ER v2 compared — incoming identity,
candidates, per-component comparison, scores, conflicts, source — as structured
diagnostics, and applies one explicit action. Merging two existing Persons only
happens here, on a reviewer's MERGE_PERSONS, audited by `PersonMergeRecord`.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from db.orm_models import (
    EntityMentionRecord,
    EventEntityMentionRecord,
    ExtractedEventRecord,
    ParsedArticleRecord,
    PersonEventLinkRecord,
    PersonRecord,
    PersonResolutionDecisionRecord,
    ReviewRecordModel,
    Source,
    SourceDocument,
)
from persons.manual_review_service import ReviewStatus, SqlAlchemyManualReviewService
from persons.models import AliasOrigin, PersonStatus
from persons.persistence import PersonMergeConflictError, SqlAlchemyPersonPersistence
from persons.resolution.aliases import AliasPromotionPolicy
from persons.resolution.candidates import load_candidates
from persons.resolution.features import PersonResolutionFeatureExtractor
from persons.resolution.models import CandidateSource, PersonIdentityInput
from persons.resolution.service import REVIEW_SUBJECT_TYPE, DecisionStatus
from persons.resolver import RuleBasedPersonResolver

logger = logging.getLogger("person_resolution")


class ResolutionReviewAction(StrEnum):
    LINK_TO_PERSON = "link_to_person"
    CREATE_NEW_PERSON = "create_new_person"
    # For possible duplicate canonical persons.
    MERGE_PERSONS = "merge_persons"
    KEEP_SEPARATE = "keep_separate"


class ResolutionReviewError(Exception):
    pass


class ResolutionReviewNotFoundError(ResolutionReviewError):
    pass


class ResolutionReviewStateError(ResolutionReviewError):
    """The decision is not pending, or the action conflicts with current data."""


class ReviewComponent(BaseModel):
    match: str
    similarity: float | None = None


class ReviewCandidate(BaseModel):
    person_id: int
    canonical_name: str
    person_status: str | None
    aliases: list[str]
    sources: list[str]
    resolution_score: float
    compared_form: str
    compared_form_is_alias: bool
    surname: ReviewComponent
    given_name: ReviewComponent
    patronymic: ReviewComponent
    order_differs: bool
    exact_alias: bool
    trigram_similarity: float | None
    semantic_similarity: float | None
    conflicts: list[str]
    score_rules: list[str]


class ReviewSource(BaseModel):
    article_id: int | None
    title: str | None = None
    url: str | None = None
    source_name: str | None = None


class ResolutionReviewView(BaseModel):
    decision_id: int
    review_id: int | None
    status: str
    mention_id: int
    incoming_name: str
    surface_text: str | None
    normalized_form: str | None
    reasons: list[str]
    decision_margin: float | None
    semantic_source: str
    source: ReviewSource
    candidates: list[ReviewCandidate] = Field(default_factory=list)
    resolver_version: str
    created_at: datetime


class ResolutionReviewResult(BaseModel):
    decision_id: int
    action: ResolutionReviewAction
    person_id: int
    created_person: bool = False
    merge_record_id: int | None = None
    events_linked: int = 0


def _view_candidate(raw: dict[str, Any], status: str | None) -> ReviewCandidate:
    candidate, features, score = raw["candidate"], raw["features"], raw["score"]
    return ReviewCandidate(
        person_id=candidate["person_id"],
        canonical_name=candidate["canonical_name"],
        person_status=status,
        aliases=candidate["aliases"],
        sources=candidate["sources"],
        resolution_score=score["resolution_score"],
        compared_form=features["compared_form"],
        compared_form_is_alias=features["compared_form_is_alias"],
        surname=ReviewComponent(
            match=features["surname"], similarity=features["surname_similarity"]
        ),
        given_name=ReviewComponent(
            match=features["given_name"], similarity=features["given_name_similarity"]
        ),
        patronymic=ReviewComponent(
            match=features["patronymic"], similarity=features["patronymic_similarity"]
        ),
        order_differs=features["order_differs"],
        exact_alias=features["exact_alias"],
        trigram_similarity=candidate["trigram_similarity"],
        semantic_similarity=candidate["semantic_similarity"],
        conflicts=features["conflicts"],
        score_rules=score["rules"],
    )


class PersonResolutionReviewService:
    def __init__(
        self,
        persistence: SqlAlchemyPersonPersistence,
        *,
        resolver: RuleBasedPersonResolver | None = None,
        reviews: SqlAlchemyManualReviewService | None = None,
        alias_policy: AliasPromotionPolicy | None = None,
        extractor: PersonResolutionFeatureExtractor | None = None,
    ) -> None:
        self._persistence = persistence
        self._resolver = resolver or RuleBasedPersonResolver(persistence)
        self._reviews = reviews or SqlAlchemyManualReviewService()
        self._alias_policy = alias_policy or AliasPromotionPolicy()
        self._extractor = extractor or PersonResolutionFeatureExtractor()

    def list_pending(self, session: Session, *, limit: int = 100) -> list[ResolutionReviewView]:
        ids = session.scalars(
            select(PersonResolutionDecisionRecord.id)
            .where(PersonResolutionDecisionRecord.status == DecisionStatus.PENDING_REVIEW.value)
            .order_by(PersonResolutionDecisionRecord.created_at, PersonResolutionDecisionRecord.id)
            .limit(limit)
        ).all()
        return [self.get(session, decision_id) for decision_id in ids]

    def get(self, session: Session, decision_id: int) -> ResolutionReviewView:
        record = session.get(PersonResolutionDecisionRecord, decision_id)
        if record is None:
            raise ResolutionReviewNotFoundError(
                f"person resolution decision {decision_id} not found"
            )
        person_ids = [raw["candidate"]["person_id"] for raw in record.candidates]
        statuses = {
            row.id: row.status
            for row in session.execute(
                select(PersonRecord.id, PersonRecord.status).where(PersonRecord.id.in_(person_ids))
            )
        }
        identity = record.identity
        return ResolutionReviewView(
            decision_id=record.id,
            review_id=self._review_id(session, record.id),
            status=record.status,
            mention_id=record.mention_id,
            incoming_name=identity["name"],
            surface_text=identity.get("surface_text"),
            normalized_form=(identity.get("normalized") or {}).get("canonical_form"),
            reasons=record.reasons,
            decision_margin=record.decision_margin,
            semantic_source=record.semantic_source,
            source=self._source(session, identity.get("article_id")),
            candidates=[
                _view_candidate(raw, statuses.get(raw["candidate"]["person_id"]))
                for raw in record.candidates
            ],
            resolver_version=record.resolver_version,
            created_at=record.created_at,
        )

    def apply(
        self,
        session: Session,
        decision_id: int,
        action: ResolutionReviewAction,
        *,
        person_id: int | None = None,
        source_person_id: int | None = None,
        note: str | None = None,
    ) -> ResolutionReviewResult:
        record = session.get(PersonResolutionDecisionRecord, decision_id, with_for_update=True)
        if record is None:
            raise ResolutionReviewNotFoundError(
                f"person resolution decision {decision_id} not found"
            )
        if record.status != DecisionStatus.PENDING_REVIEW.value:
            raise ResolutionReviewStateError(
                f"decision {decision_id} is {record.status}, not pending review"
            )
        mention = session.get_one(EntityMentionRecord, record.mention_id)
        identity = PersonIdentityInput(
            name=record.identity["name"],
            surface_text=record.identity.get("surface_text"),
            matching_key=record.identity.get("matching_key"),
            mention_id=mention.id,
        )

        created = False
        merge_record_id = None
        if action is ResolutionReviewAction.CREATE_NEW_PERSON:
            target = self._create_person(session, identity, mention)
            created = True
        elif action is ResolutionReviewAction.MERGE_PERSONS:
            if person_id is None or source_person_id is None or person_id == source_person_id:
                raise ResolutionReviewStateError(
                    "merge_persons needs distinct person_id (target) and source_person_id"
                )
            self._require_active(session, person_id)
            self._require_active(session, source_person_id)
            try:
                merge_record_id = self._persistence.merge_persons_in_session(
                    session,
                    source_person_id=source_person_id,
                    target_person_id=person_id,
                    reason=(
                        f"person resolution review of decision {decision_id}: {note or ''}"
                    ).strip(),
                )
            except PersonMergeConflictError as exc:
                raise ResolutionReviewStateError(str(exc)) from exc
            target = person_id
        else:
            if person_id is None:
                raise ResolutionReviewStateError(f"{action.value} needs person_id")
            self._require_active(session, person_id)
            target = person_id
            self._promote_alias(session, identity, mention, target)

        mention.person_id = target
        events_linked = _link_mention_events(session, mention.id, target)
        record.status = DecisionStatus.REVIEWED.value
        record.review_action = action.value
        record.selected_person_id = target
        record.reviewer_note = note
        record.reviewed_at = datetime.now(UTC)
        review_id = self._review_id(session, record.id)
        if review_id is not None:
            self._reviews.update_review_status(
                session, review_id, ReviewStatus.APPROVED, f"{action.value}: {note or ''}".strip()
            )
        session.flush()
        logger.info(
            "er_review_applied decision_id=%s action=%s person_id=%s merge_record_id=%s",
            decision_id,
            action.value,
            target,
            merge_record_id,
        )
        return ResolutionReviewResult(
            decision_id=decision_id,
            action=action,
            person_id=target,
            created_person=created,
            merge_record_id=merge_record_id,
            events_linked=events_linked,
        )

    def _create_person(
        self, session: Session, identity: PersonIdentityInput, mention: EntityMentionRecord
    ) -> int:
        key = identity.matching_key or ""
        existing = self._persistence.find_person_by_matching_key_in_session(session, key)
        if existing is not None:
            raise ResolutionReviewStateError(
                f"person {existing} already has this matching_key; link to it instead"
            )
        person_id = self._persistence.create_person_in_session(
            session, canonical_name=identity.name, normalized_name=identity.name, matching_key=key
        )
        self._resolver.add_alias_if_not_exists(
            person_id=person_id,
            surface_text=mention.surface_text,
            normalized_text=identity.name,
            matching_key=key,
            origin=AliasOrigin.MANUAL,
            confidence=mention.confidence,
            source_mention_id=mention.id,
            session=session,
        )
        return person_id

    def _promote_alias(
        self,
        session: Session,
        identity: PersonIdentityInput,
        mention: EntityMentionRecord,
        person_id: int,
    ) -> None:
        candidate = load_candidates(session, [person_id], CandidateSource.ALIAS)[person_id]
        features = self._extractor.extract(identity, candidate)
        if self._alias_policy.should_promote(identity.name, features):
            self._resolver.add_alias_if_not_exists(
                person_id=person_id,
                surface_text=mention.surface_text,
                normalized_text=identity.name,
                matching_key=identity.matching_key or "",
                origin=AliasOrigin.MANUAL,
                confidence=mention.confidence,
                source_mention_id=mention.id,
                session=session,
            )

    @staticmethod
    def _require_active(session: Session, person_id: int) -> None:
        status = session.scalar(select(PersonRecord.status).where(PersonRecord.id == person_id))
        if status != PersonStatus.ACTIVE.value:
            raise ResolutionReviewStateError(f"person {person_id} is not active ({status})")

    @staticmethod
    def _review_id(session: Session, decision_id: int) -> int | None:
        return session.scalar(
            select(ReviewRecordModel.id)
            .where(
                ReviewRecordModel.subject_type == REVIEW_SUBJECT_TYPE,
                ReviewRecordModel.subject_id == decision_id,
            )
            .order_by(ReviewRecordModel.id.desc())
            .limit(1)
        )

    @staticmethod
    def _source(session: Session, article_id: int | None) -> ReviewSource:
        if article_id is None:
            return ReviewSource(article_id=None)
        row = session.execute(
            select(ParsedArticleRecord.title, SourceDocument.canonical_url, Source.name)
            .join(SourceDocument, SourceDocument.id == ParsedArticleRecord.document_id)
            .join(Source, Source.id == SourceDocument.source_id)
            .where(ParsedArticleRecord.id == article_id)
        ).one_or_none()
        if row is None:
            return ReviewSource(article_id=article_id)
        return ReviewSource(
            article_id=article_id, title=row.title, url=row.canonical_url, source_name=row.name
        )


def _link_mention_events(session: Session, mention_id: int, person_id: int) -> int:
    """Person-event links for the events this mention takes part in (idempotent)."""
    linked = 0
    rows = session.execute(
        select(
            EventEntityMentionRecord.event_id,
            EventEntityMentionRecord.role,
            ExtractedEventRecord.confidence,
        )
        .join(ExtractedEventRecord, ExtractedEventRecord.id == EventEntityMentionRecord.event_id)
        .where(EventEntityMentionRecord.mention_id == mention_id)
    ).all()
    for event_id, role, confidence in rows:
        exists = session.scalar(
            select(PersonEventLinkRecord.id).where(
                PersonEventLinkRecord.person_id == person_id,
                PersonEventLinkRecord.event_id == event_id,
                PersonEventLinkRecord.role == role,
            )
        )
        if exists is None:
            session.add(
                PersonEventLinkRecord(
                    person_id=person_id, event_id=event_id, role=role, confidence=confidence
                )
            )
            linked += 1
    session.flush()
    return linked
