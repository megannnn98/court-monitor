"""Application service: AI review of pending ER v2 decisions, then the ER action.

One decision at a time, each in its own transaction: the audit row and the action it
allowed are committed together, so an applied link always has provenance. A failure of
one decision never stops the batch, and a failure of the provider never merges anybody.

Nothing here talks to a model directly: the reviewer is an `EntityMatchReviewer`.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Sequence

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from db.orm_models import PersonResolutionAiReviewRecord, PersonResolutionDecisionRecord
from persons.persistence import SqlAlchemyPersonPersistence
from persons.resolution.ai_context import build_review_context
from persons.resolution.ai_policy import (
    CandidateReviewOutcome,
    EntityReviewOutcome,
    EntityReviewPolicy,
    EntityReviewSettings,
    ReviewResolution,
)
from persons.resolution.ai_review import (
    DecisionReviewContext,
    EntityMatchReviewer,
    EntityReviewError,
    EntityReviewRequest,
    EntityReviewResult,
)
from persons.resolution.review import (
    PersonResolutionReviewService,
    ResolutionReviewAction,
    ResolutionReviewError,
)
from persons.resolution.service import DecisionStatus

logger = logging.getLogger("person_resolution")

# Backoff between attempts of a transient provider failure: 0.5s, 1s, 2s, …
BASE_RETRY_DELAY_SECONDS = 0.5


class _ProviderFailure(Exception):
    """A review that never produced an answer, with the calls it really cost."""

    def __init__(self, error: EntityReviewError, provider_calls: int) -> None:
        super().__init__(str(error))
        self.error = error
        self.provider_calls = provider_calls


class EntityReviewBatchResult(BaseModel):
    reviewed: int = 0
    auto_accepted: int = 0
    auto_rejected: int = 0
    human_required: int = 0
    failed: int = 0
    # Decisions already reviewed with this input, model and prompt version.
    skipped: int = 0

    def count(self, outcome: EntityReviewOutcome) -> None:
        self.reviewed += 1
        if outcome is EntityReviewOutcome.AUTO_ACCEPTED:
            self.auto_accepted += 1
        elif outcome is EntityReviewOutcome.AUTO_REJECTED:
            self.auto_rejected += 1
        elif outcome is EntityReviewOutcome.HUMAN_REQUIRED:
            self.human_required += 1
        else:
            self.failed += 1


class AutomatedEntityReviewService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        reviewer: EntityMatchReviewer,
        *,
        settings: EntityReviewSettings,
        model: str,
        policy: EntityReviewPolicy | None = None,
        review_service: PersonResolutionReviewService | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._session_factory = session_factory
        self._reviewer = reviewer
        self._settings = settings
        self._model = model
        self._policy = policy or EntityReviewPolicy(auto_threshold=settings.auto_threshold)
        self._review_service = review_service or PersonResolutionReviewService(
            SqlAlchemyPersonPersistence(session_factory)
        )
        self._sleep = sleep

    def review_pending(self, *, limit: int = 100) -> EntityReviewBatchResult:
        """Review the oldest pending decisions and apply what the policy allows."""
        result = EntityReviewBatchResult()
        for decision_id in self._pending_decision_ids(limit):
            try:
                self._review_one(decision_id, result)
            except Exception:
                logger.exception("event=entity_review_decision_failed decision_id=%s", decision_id)
                result.count(EntityReviewOutcome.FAILED)
        logger.info(
            "event=entity_review_batch_finished reviewed=%s auto_accepted=%s auto_rejected=%s "
            "human_required=%s failed=%s skipped=%s model=%s prompt_version=%s",
            result.reviewed,
            result.auto_accepted,
            result.auto_rejected,
            result.human_required,
            result.failed,
            result.skipped,
            self._model,
            self._settings.prompt_version,
        )
        return result

    def _pending_decision_ids(self, limit: int) -> list[int]:
        with self._session_factory() as session:
            return list(
                session.scalars(
                    select(PersonResolutionDecisionRecord.id)
                    .where(
                        PersonResolutionDecisionRecord.status == DecisionStatus.PENDING_REVIEW.value
                    )
                    .order_by(
                        PersonResolutionDecisionRecord.created_at,
                        PersonResolutionDecisionRecord.id,
                    )
                    .limit(limit)
                ).all()
            )

    def _review_one(self, decision_id: int, result: EntityReviewBatchResult) -> None:
        """Read, review, then apply — the model is called with no transaction open.

        A lock held across a provider call would keep a pooled connection and a row lock
        for as long as the provider takes (a timeout times the retries), and would block
        a human resolving the same decision. So the decision is read in one short
        transaction, the model is called outside any transaction, and the action is
        applied in a second transaction that locks the row and re-checks that the
        decision is still pending and still unreviewed."""
        prepared = self._prepare(decision_id)
        if prepared is None:
            result.skipped += 1
            return
        context, input_hash = prepared

        started = time.perf_counter()
        try:
            outcomes, provider_calls = self._review_candidates(context.requests)
        except _ProviderFailure as failure:
            duration_ms = int((time.perf_counter() - started) * 1000)
            resolution = self._policy.failure(str(failure.error))
            self._store_in_new_session(
                context,
                input_hash,
                resolution=resolution,
                provider_calls=failure.provider_calls,
                duration_ms=duration_ms,
            )
            logger.warning(
                "event=entity_review_failed decision_id=%s model=%s duration_ms=%s "
                "provider_calls=%s reason=%s",
                decision_id,
                self._model,
                duration_ms,
                failure.provider_calls,
                resolution.reason,
            )
            result.count(EntityReviewOutcome.FAILED)
            return
        duration_ms = int((time.perf_counter() - started) * 1000)

        with self._session_factory() as session:
            decision = session.get(
                PersonResolutionDecisionRecord, decision_id, with_for_update=True
            )
            if (
                decision is None
                or decision.status != DecisionStatus.PENDING_REVIEW.value
                or self._already_reviewed(session, decision_id, input_hash)
            ):
                # A human or another worker resolved it while the model was answering.
                result.skipped += 1
                return
            resolution = self._policy.resolve(context, outcomes)
            record = self._store(
                session,
                context,
                input_hash,
                outcomes=outcomes,
                resolution=resolution,
                provider_calls=provider_calls,
                duration_ms=duration_ms,
            )
            if resolution.applies:
                try:
                    self._apply(session, decision_id, resolution)
                except ResolutionReviewError as exc:
                    # The action no longer fits the data (the person was deactivated or
                    # merged): roll the audit back with it, then record the failure so
                    # the decision stays with a human.
                    session.rollback()
                    self._store_in_new_session(
                        context,
                        input_hash,
                        resolution=self._policy.failure(f"{type(exc).__name__}: {exc}"),
                        provider_calls=provider_calls,
                        duration_ms=duration_ms,
                    )
                    logger.warning(
                        "event=entity_review_apply_failed decision_id=%s action=%s error=%s",
                        decision_id,
                        None if resolution.action is None else resolution.action.value,
                        type(exc).__name__,
                    )
                    result.count(EntityReviewOutcome.FAILED)
                    return
            session.commit()
            self._log_result(decision_id, record, resolution, provider_calls, duration_ms)
            result.count(resolution.outcome)

    def _prepare(self, decision_id: int) -> tuple[DecisionReviewContext, str] | None:
        """The review input of a pending, not yet reviewed decision. None: nothing to do."""
        with self._session_factory() as session:
            decision = session.get(PersonResolutionDecisionRecord, decision_id)
            if decision is None or decision.status != DecisionStatus.PENDING_REVIEW.value:
                return None
            context = build_review_context(session, decision)
            input_hash = context.input_hash()
            if self._already_reviewed(session, decision_id, input_hash):
                return None
            return context, input_hash

    def _review_candidates(
        self, requests: Sequence[EntityReviewRequest]
    ) -> tuple[list[CandidateReviewOutcome], int]:
        """Every candidate's answer, and how many provider calls they cost in total."""
        outcomes: list[CandidateReviewOutcome] = []
        calls = 0
        for request in requests:
            try:
                result, used = self._review_with_retries(request)
            except _ProviderFailure as failure:
                raise _ProviderFailure(failure.error, calls + failure.provider_calls) from None
            calls += used
            outcomes.append(CandidateReviewOutcome(request=request, result=result))
        return outcomes, calls

    def _review_with_retries(self, request: EntityReviewRequest) -> tuple[EntityReviewResult, int]:
        """Retry a transient provider failure only; a broken contract is final."""
        call = 0
        while True:
            call += 1
            try:
                return self._reviewer.review(request), call
            except EntityReviewError as exc:
                if not exc.transient or call > self._settings.max_retries:
                    raise _ProviderFailure(exc, call) from None
                self._sleep(BASE_RETRY_DELAY_SECONDS * 2 ** (call - 1))

    def _already_reviewed(self, session: Session, decision_id: int, input_hash: str) -> bool:
        return (
            session.scalar(
                select(func.count())
                .select_from(PersonResolutionAiReviewRecord)
                .where(
                    PersonResolutionAiReviewRecord.decision_id == decision_id,
                    PersonResolutionAiReviewRecord.input_hash == input_hash,
                    PersonResolutionAiReviewRecord.model == self._model,
                    PersonResolutionAiReviewRecord.prompt_version == self._settings.prompt_version,
                    # A failed review never produced an answer: the next run tries again.
                    PersonResolutionAiReviewRecord.outcome != EntityReviewOutcome.FAILED.value,
                )
            )
            or 0
        ) > 0

    def _store(
        self,
        session: Session,
        context: DecisionReviewContext,
        input_hash: str,
        *,
        outcomes: Sequence[CandidateReviewOutcome] = (),
        resolution: ReviewResolution,
        provider_calls: int,
        duration_ms: int,
    ) -> PersonResolutionAiReviewRecord:
        applied = next(
            (
                outcome
                for outcome in outcomes
                if resolution.person_id is not None and outcome.person_id == resolution.person_id
            ),
            outcomes[0] if outcomes else None,
        )
        record = PersonResolutionAiReviewRecord(
            decision_id=context.decision_id,
            provider=self._settings.provider.value,
            model=self._model,
            prompt_version=self._settings.prompt_version,
            input_hash=input_hash,
            decision=None if applied is None else applied.result.decision.value,
            confidence=None if applied is None else applied.result.confidence,
            explanation=None if applied is None else applied.result.explanation,
            supporting_evidence=[] if applied is None else applied.result.supporting_evidence,
            conflicting_evidence=[] if applied is None else applied.result.conflicting_evidence,
            candidate_reviews=[
                {
                    "person_id": outcome.person_id,
                    "canonical_name": outcome.request.candidate.canonical_name,
                    "deterministic_score": outcome.request.deterministic_score,
                    "conflicting_features": list(outcome.request.conflicting_features),
                    "result": outcome.result.model_dump(mode="json"),
                }
                for outcome in outcomes
            ],
            outcome=resolution.outcome.value,
            applied_action=None if resolution.action is None else resolution.action.value,
            applied_person_id=resolution.person_id,
            resolution_reason=resolution.reason,
            provider_calls=provider_calls,
            duration_ms=duration_ms,
        )
        session.add(record)
        session.flush()
        return record

    def _store_in_new_session(
        self,
        context: DecisionReviewContext,
        input_hash: str,
        *,
        resolution: ReviewResolution,
        provider_calls: int,
        duration_ms: int,
    ) -> None:
        """A failure row of its own: the audit outlives the transaction that failed."""
        with self._session_factory() as session:
            self._store(
                session,
                context,
                input_hash,
                resolution=resolution,
                provider_calls=provider_calls,
                duration_ms=duration_ms,
            )
            session.commit()

    def _apply(self, session: Session, decision_id: int, resolution: ReviewResolution) -> None:
        assert resolution.action is not None
        note = f"ai review ({self._model}, {self._settings.prompt_version}): {resolution.reason}"
        self._review_service.apply(
            session,
            decision_id,
            resolution.action,
            person_id=resolution.person_id
            if resolution.action is ResolutionReviewAction.LINK_TO_PERSON
            else None,
            note=note,
        )

    def _log_result(
        self,
        decision_id: int,
        record: PersonResolutionAiReviewRecord,
        resolution: ReviewResolution,
        provider_calls: int,
        duration_ms: int,
    ) -> None:
        """Identifiers and the decision only: no prompt, no answer text, no article text."""
        logger.info(
            "event=entity_review_decided decision_id=%s review_id=%s model=%s prompt_version=%s "
            "duration_ms=%s provider_calls=%s decision=%s confidence=%s outcome=%s action=%s",
            decision_id,
            record.id,
            self._model,
            self._settings.prompt_version,
            duration_ms,
            provider_calls,
            record.decision,
            None if record.confidence is None else round(record.confidence, 2),
            resolution.outcome.value,
            None if resolution.action is None else resolution.action.value,
        )
