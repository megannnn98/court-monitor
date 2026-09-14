"""ER v2 orchestration: exact fast-path → candidates → features → score → decision.

`PersonResolutionEngine` only reads (CLI dry-run, evaluation). The
`PersonResolutionService` applies a decision to a mention inside the caller's
transaction and records its provenance in `person_resolution_decisions`.

Invariant: ER v2 links a new mention to an existing Person or creates one; it
never merges two existing canonical Persons.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from db.orm_models import (
    ArticleExtractionRunRecord,
    EntityMentionRecord,
    PersonResolutionDecisionRecord,
)
from persons.manual_review_service import SqlAlchemyManualReviewService
from persons.models import AliasOrigin
from persons.persistence import SqlAlchemyPersonPersistence
from persons.resolution.aliases import AliasPromotionPolicy
from persons.resolution.candidates import (
    CandidateConfig,
    CandidateGenerationResult,
    CompositeCandidateGenerator,
)
from persons.resolution.decision import PersonResolutionDecisionPolicy
from persons.resolution.features import PersonResolutionFeatureExtractor
from persons.resolution.models import (
    NormalizedPersonName,
    PersonIdentityInput,
    PersonResolutionAction,
    PersonResolutionDecision,
    PersonResolutionReason,
    ScoredPersonCandidate,
    SemanticSourceStatus,
)
from persons.resolution.normalizer import PersonNameNormalizer
from persons.resolution.scoring import PersonResolutionScorer
from persons.resolver import RuleBasedPersonResolver

logger = logging.getLogger("person_resolution")

RESOLVER_VERSION = "er-v2"
REVIEW_SUBJECT_TYPE = "person_resolution"


class ResolutionMethod(StrEnum):
    EXACT_MATCHING_KEY = "exact_matching_key"
    ER_V2 = "er_v2"
    MANUAL_REVIEW = "manual_review"


class DecisionStatus(StrEnum):
    APPLIED = "applied"
    PENDING_REVIEW = "pending_review"
    REVIEWED = "reviewed"


@dataclass(frozen=True)
class ResolutionPlan:
    """Everything ER v2 concluded for one identity, before any write."""

    identity: PersonIdentityInput
    name: NormalizedPersonName
    exact_person_id: int | None
    generation: CandidateGenerationResult | None
    decision: PersonResolutionDecision

    @property
    def method(self) -> ResolutionMethod:
        if self.exact_person_id is not None:
            return ResolutionMethod.EXACT_MATCHING_KEY
        return ResolutionMethod.ER_V2

    @property
    def top(self) -> ScoredPersonCandidate | None:
        return self.decision.candidates[0] if self.decision.candidates else None


@dataclass(frozen=True)
class MentionResolutionOutcome:
    mention_id: int
    person_id: int | None
    action: PersonResolutionAction
    method: ResolutionMethod | None
    created_person: bool = False
    decision_id: int | None = None
    # True when an earlier decision or link was reused (idempotent re-run).
    reused: bool = False


class PersonResolutionEngine:
    def __init__(
        self,
        *,
        persistence: SqlAlchemyPersonPersistence,
        generator: CompositeCandidateGenerator,
        policy: PersonResolutionDecisionPolicy,
        config: CandidateConfig | None = None,
        normalizer: PersonNameNormalizer | None = None,
        extractor: PersonResolutionFeatureExtractor | None = None,
        scorer: PersonResolutionScorer | None = None,
    ) -> None:
        self._persistence = persistence
        self._generator = generator
        self._policy = policy
        self._config = config or CandidateConfig()
        self._normalizer = normalizer or PersonNameNormalizer()
        self._extractor = extractor or PersonResolutionFeatureExtractor(self._normalizer)
        self._scorer = scorer or PersonResolutionScorer()

    @property
    def normalizer(self) -> PersonNameNormalizer:
        return self._normalizer

    def plan(self, identity: PersonIdentityInput, session: Session) -> ResolutionPlan:
        name = self._normalizer.normalize(identity.name)
        if identity.matching_key:
            exact = self._persistence.find_person_by_matching_key_in_session(
                session, identity.matching_key
            )
            if exact is not None:
                logger.info("er_exact_match mention_id=%s person_id=%s", identity.mention_id, exact)
                return ResolutionPlan(
                    identity=identity,
                    name=name,
                    exact_person_id=exact,
                    generation=None,
                    decision=PersonResolutionDecision(
                        action=PersonResolutionAction.AUTO_LINK,
                        selected_person_id=exact,
                        reasons=[PersonResolutionReason.EXACT_MATCHING_KEY],
                    ),
                )

        generation = self._generator.generate(
            identity, limit=self._config.candidate_limit, session=session
        )
        logger.info(
            "er_candidates_generated mention_id=%s count=%d sources=%s semantic=%s",
            identity.mention_id,
            len(generation.candidates),
            {source.value: count for source, count in generation.counts.items()},
            generation.semantic_source.value,
        )
        scored = []
        for candidate in generation.candidates:
            features = self._extractor.extract(identity, candidate)
            score = self._scorer.score(features)
            logger.debug(
                "er_candidate_scored mention_id=%s person_id=%s score=%.4f conflicts=%s",
                identity.mention_id,
                candidate.person_id,
                score.resolution_score,
                [conflict.value for conflict in features.conflicts],
            )
            scored.append(
                ScoredPersonCandidate(candidate=candidate, features=features, score=score)
            )
        decision = self._policy.decide(identity, scored, semantic_source=generation.semantic_source)
        logger.info(
            "er_decision mention_id=%s action=%s person_id=%s score=%s margin=%s reasons=%s",
            identity.mention_id,
            decision.action.value,
            decision.selected_person_id,
            decision.candidates[0].resolution_score if decision.candidates else None,
            decision.decision_margin,
            [reason.value for reason in decision.reasons],
        )
        return ResolutionPlan(
            identity=identity,
            name=name,
            exact_person_id=None,
            generation=generation,
            decision=decision,
        )


def identity_from_mention(
    mention: EntityMentionRecord, *, article_id: int | None = None
) -> PersonIdentityInput | None:
    data = mention.normalized_data
    matching_key = data.get("matching_key")
    if not isinstance(matching_key, str) or not matching_key:
        return None
    full_name = data.get("full_name")
    return PersonIdentityInput(
        name=full_name if isinstance(full_name, str) and full_name else mention.normalized_text,
        surface_text=mention.surface_text,
        matching_key=matching_key,
        mention_id=mention.id,
        article_id=article_id,
    )


def advisory_lock_keys(names: Iterable[NormalizedPersonName]) -> list[str]:
    """Sorted, de-duplicated identity-block keys: one lock order for every worker."""
    return sorted({f"person_block:{key}" for name in names for key in name.block_keys})


class PersonResolutionService:
    def __init__(
        self,
        *,
        engine: PersonResolutionEngine,
        persistence: SqlAlchemyPersonPersistence,
        resolver: RuleBasedPersonResolver,
        review_service: SqlAlchemyManualReviewService | None = None,
        alias_policy: AliasPromotionPolicy | None = None,
    ) -> None:
        self._engine = engine
        self._persistence = persistence
        self._resolver = resolver
        self._reviews = review_service or SqlAlchemyManualReviewService()
        self._alias_policy = alias_policy or AliasPromotionPolicy(engine.normalizer)

    def lock_identity_blocks(
        self, session: Session, identities: Iterable[PersonIdentityInput]
    ) -> None:
        """Serialize resolution of possibly-same identities across workers.

        Extra protection on top of `uq_persons_matching_key_active` + retry, which
        only covers equal keys: "Иван Иванов" and "Иванов Иван" share a block.
        Transaction-scoped, taken up front in sorted order (no deadlocks).
        """
        names = [self._engine.normalizer.normalize(identity.name) for identity in identities]
        for key in advisory_lock_keys(names):
            session.execute(select(func.pg_advisory_xact_lock(func.hashtextextended(key, 0))))

    def resolve_mention(
        self, session: Session, mention: EntityMentionRecord
    ) -> MentionResolutionOutcome | None:
        article_id = session.scalar(
            select(ArticleExtractionRunRecord.article_id).where(
                ArticleExtractionRunRecord.id == mention.extraction_run_id
            )
        )
        identity = identity_from_mention(mention, article_id=article_id)
        if identity is None:
            return None

        existing = self._existing_outcome(session, mention)
        if existing is not None:
            return existing

        logger.info("er_resolution_started mention_id=%s", mention.id)
        plan = self._engine.plan(identity, session)
        decision_id = self._record(session, mention, plan)
        if decision_id is None:  # a concurrent worker recorded this mention first
            session.refresh(mention)
            return self._existing_outcome(session, mention)

        decision = plan.decision
        if decision.action is PersonResolutionAction.REVIEW:
            review_id, _ = self._reviews.get_or_create_pending_review(
                session,
                review_type=REVIEW_SUBJECT_TYPE,
                entity_id=decision_id,
                reason=",".join(reason.value for reason in decision.reasons),
            )
            logger.info(
                "er_review_required mention_id=%s decision_id=%s review_id=%s",
                mention.id,
                decision_id,
                review_id,
            )
            return MentionResolutionOutcome(
                mention_id=mention.id,
                person_id=None,
                action=decision.action,
                method=plan.method,
                decision_id=decision_id,
            )

        created = False
        if plan.exact_person_id is not None or decision.action is PersonResolutionAction.CREATE_NEW:
            # Existing exact resolver: alias for the exact key, racing-safe creation.
            result = self._resolver.resolve_and_create(
                normalized_text=identity.name,
                matching_key=identity.matching_key or "",
                surface_text=mention.surface_text,
                origin=AliasOrigin.EXTRACTION,
                confidence=mention.confidence,
                source_mention_id=mention.id,
                session=session,
            )
            if result.person_id is None:
                raise RuntimeError(f"exact resolver returned no person for mention {mention.id}")
            person_id = result.person_id
            created = decision.action is PersonResolutionAction.CREATE_NEW
            if created:
                logger.info("er_person_created mention_id=%s person_id=%s", mention.id, person_id)
        else:
            selected = next(
                (c for c in decision.candidates if c.person_id == decision.selected_person_id),
                None,
            )
            if selected is None:
                raise RuntimeError(f"auto-link without a selected person for mention {mention.id}")
            person_id = selected.person_id
            if self._alias_policy.should_promote(identity.name, selected.features):
                self._resolver.add_alias_if_not_exists(
                    person_id=person_id,
                    surface_text=mention.surface_text,
                    normalized_text=identity.name,
                    matching_key=identity.matching_key or "",
                    origin=AliasOrigin.RESOLUTION,
                    confidence=mention.confidence,
                    source_mention_id=mention.id,
                    session=session,
                )

        mention.person_id = person_id
        session.get_one(PersonResolutionDecisionRecord, decision_id).selected_person_id = person_id
        session.flush()
        logger.info(
            "er_person_linked mention_id=%s person_id=%s method=%s",
            mention.id,
            person_id,
            plan.method.value,
        )
        return MentionResolutionOutcome(
            mention_id=mention.id,
            person_id=person_id,
            action=decision.action,
            method=plan.method,
            created_person=created,
            decision_id=decision_id,
        )

    def _existing_outcome(
        self, session: Session, mention: EntityMentionRecord
    ) -> MentionResolutionOutcome | None:
        record = session.scalar(
            select(PersonResolutionDecisionRecord).where(
                PersonResolutionDecisionRecord.mention_id == mention.id,
                PersonResolutionDecisionRecord.resolver_version == RESOLVER_VERSION,
            )
        )
        if record is not None:
            return MentionResolutionOutcome(
                mention_id=mention.id,
                person_id=mention.person_id,
                action=PersonResolutionAction(record.action),
                method=ResolutionMethod(record.method),
                decision_id=record.id,
                reused=True,
            )
        if mention.person_id is not None:
            # Linked before ER v2 existed: history is not re-resolved implicitly.
            return MentionResolutionOutcome(
                mention_id=mention.id,
                person_id=mention.person_id,
                action=PersonResolutionAction.AUTO_LINK,
                method=None,
                reused=True,
            )
        return None

    def _record(
        self, session: Session, mention: EntityMentionRecord, plan: ResolutionPlan
    ) -> int | None:
        decision = plan.decision
        top = plan.top
        return session.scalar(
            insert(PersonResolutionDecisionRecord)
            .values(
                mention_id=mention.id,
                resolver_version=RESOLVER_VERSION,
                method=plan.method.value,
                action=decision.action.value,
                status=(
                    DecisionStatus.PENDING_REVIEW
                    if decision.action is PersonResolutionAction.REVIEW
                    else DecisionStatus.APPLIED
                ).value,
                selected_person_id=decision.selected_person_id,
                resolution_score=(
                    1.0
                    if plan.exact_person_id is not None
                    else top.resolution_score
                    if top
                    else None
                ),
                decision_margin=decision.decision_margin,
                reasons=[reason.value for reason in decision.reasons],
                identity={
                    "name": plan.identity.name,
                    "surface_text": plan.identity.surface_text,
                    "matching_key": plan.identity.matching_key,
                    "article_id": plan.identity.article_id,
                    "normalized": plan.name.model_dump(mode="json"),
                },
                candidates=[candidate.model_dump(mode="json") for candidate in decision.candidates],
                semantic_source=(
                    plan.generation.semantic_source
                    if plan.generation
                    else SemanticSourceStatus.DISABLED
                ).value,
                created_at=datetime.now(UTC),
            )
            .on_conflict_do_nothing(constraint="uq_person_resolution_decisions_mention_version")
            .returning(PersonResolutionDecisionRecord.id)
        )
