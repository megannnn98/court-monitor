"""What an AI review is allowed to do: deterministic, configurable, auditable.

The model answers; this policy decides. It is the only place that turns AI answers into
an ER action, and it never lets a model confidence override an ER conflict: a candidate
with conflicting identity data, several namesakes or possibly duplicate persons stays
with a human whatever the model says. Merging two canonical persons is never automatic.
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from persons.resolution.ai_review import (
    ENTITY_REVIEW_PROMPT_VERSION,
    DecisionReviewContext,
    EntityReviewDecision,
    EntityReviewRequest,
    EntityReviewResult,
)
from persons.resolution.models import PersonResolutionReason
from persons.resolution.review import ResolutionReviewAction

DEFAULT_AUTO_THRESHOLD = 0.90
DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_MAX_RETRIES = 3

# Reasons no model confidence may resolve: telling namesakes apart or deciding that two
# canonical persons are one is a human decision (ADR 0012, ADR 0020).
BLOCKING_REASONS = frozenset(
    {
        PersonResolutionReason.MULTIPLE_EXACT_NAME_MATCHES,
        PersonResolutionReason.POSSIBLE_DUPLICATE_PERSONS,
        PersonResolutionReason.KNOWN_DISTINCT_PERSONS,
    }
)


class EntityReviewOutcome(StrEnum):
    AUTO_ACCEPTED = "auto_accepted"
    AUTO_REJECTED = "auto_rejected"
    HUMAN_REQUIRED = "human_required"
    FAILED = "failed"


class EntityReviewProvider(StrEnum):
    TOGETHER = "together"
    # No reviewer configured: pending decisions keep going to a human as before.
    NONE = "none"


class EntityReviewConfigurationError(ValueError):
    pass


@dataclass(frozen=True)
class EntityReviewSettings:
    provider: EntityReviewProvider = EntityReviewProvider.NONE
    # None: the provider's own configured model (TOGETHER_MODEL).
    model: str | None = None
    auto_threshold: float = DEFAULT_AUTO_THRESHOLD
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    max_retries: int = DEFAULT_MAX_RETRIES
    prompt_version: str = ENTITY_REVIEW_PROMPT_VERSION

    def __post_init__(self) -> None:
        if not 0.5 <= self.auto_threshold <= 1.0:
            raise EntityReviewConfigurationError(
                "ENTITY_REVIEW_AUTO_THRESHOLD must be within [0.5, 1.0]"
            )
        if self.timeout_seconds <= 0:
            raise EntityReviewConfigurationError("ENTITY_REVIEW_TIMEOUT_SECONDS must be positive")
        if self.max_retries < 0:
            raise EntityReviewConfigurationError("ENTITY_REVIEW_MAX_RETRIES must not be negative")
        if not self.prompt_version.strip():
            raise EntityReviewConfigurationError("ENTITY_REVIEW_PROMPT_VERSION must not be empty")

    @property
    def enabled(self) -> bool:
        return self.provider is not EntityReviewProvider.NONE

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> EntityReviewSettings:
        env = os.environ if env is None else env
        defaults = cls()
        raw_provider = (env.get("ENTITY_REVIEW_PROVIDER") or "").strip().lower()
        if raw_provider and raw_provider not in set(EntityReviewProvider):
            allowed = ", ".join(provider.value for provider in EntityReviewProvider)
            raise EntityReviewConfigurationError(
                f"ENTITY_REVIEW_PROVIDER must be one of {allowed}, got {raw_provider!r}"
            )
        return cls(
            provider=EntityReviewProvider(raw_provider) if raw_provider else defaults.provider,
            model=(env.get("ENTITY_REVIEW_MODEL") or "").strip() or None,
            auto_threshold=_float(env, "ENTITY_REVIEW_AUTO_THRESHOLD", defaults.auto_threshold),
            timeout_seconds=_float(env, "ENTITY_REVIEW_TIMEOUT_SECONDS", defaults.timeout_seconds),
            max_retries=_int(env, "ENTITY_REVIEW_MAX_RETRIES", defaults.max_retries),
            prompt_version=(env.get("ENTITY_REVIEW_PROMPT_VERSION") or "").strip()
            or defaults.prompt_version,
        )


def _float(env: Mapping[str, str], name: str, default: float) -> float:
    raw = (env.get(name) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        raise EntityReviewConfigurationError(f"{name} must be a number, got {raw!r}") from None


def _int(env: Mapping[str, str], name: str, default: int) -> int:
    raw = (env.get(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        raise EntityReviewConfigurationError(
            f"{name} must be a whole number, got {raw!r}"
        ) from None


class CandidateReviewOutcome(BaseModel):
    """One reviewed candidate: the request that was sent and the answer that came back."""

    model_config = ConfigDict(frozen=True)

    request: EntityReviewRequest
    result: EntityReviewResult

    @property
    def person_id(self) -> int:
        return self.request.candidate.person_id


class ReviewResolution(BaseModel):
    model_config = ConfigDict(frozen=True)

    outcome: EntityReviewOutcome
    # None for human_required and failed: nothing is applied.
    action: ResolutionReviewAction | None = None
    person_id: int | None = None
    # Why a human is needed, or why the automatic action is allowed.
    reason: str

    @property
    def applies(self) -> bool:
        return self.action is not None


class EntityReviewPolicy:
    def __init__(self, *, auto_threshold: float = DEFAULT_AUTO_THRESHOLD) -> None:
        self._auto_threshold = auto_threshold

    @property
    def auto_threshold(self) -> float:
        return self._auto_threshold

    def resolve(
        self, context: DecisionReviewContext, outcomes: Sequence[CandidateReviewOutcome]
    ) -> ReviewResolution:
        """The action for one pending decision, from every candidate's AI answer."""
        blocking = sorted(reason.value for reason in set(context.review_reasons) & BLOCKING_REASONS)
        if not outcomes:
            return self._human("no_candidate_to_review")
        confident = [
            outcome for outcome in outcomes if outcome.result.confidence >= self._auto_threshold
        ]
        same = [
            outcome
            for outcome in confident
            if outcome.result.decision is EntityReviewDecision.SAME_PERSON
        ]
        if same:
            if blocking:
                return self._human(f"blocking_reasons:{','.join(blocking)}")
            if len(same) > 1:
                return self._human("several_candidates_reviewed_as_same_person")
            chosen = same[0]
            if chosen.request.conflicting_features:
                return self._human(
                    "conflicting_identity_data:"
                    + ",".join(sorted(chosen.request.conflicting_features))
                )
            return ReviewResolution(
                outcome=EntityReviewOutcome.AUTO_ACCEPTED,
                action=ResolutionReviewAction.LINK_TO_PERSON,
                person_id=chosen.person_id,
                reason=(
                    f"same_person confidence {chosen.result.confidence:.2f} "
                    f">= {self._auto_threshold:.2f}"
                ),
            )
        if len(confident) == len(outcomes) and all(
            outcome.result.decision is EntityReviewDecision.DIFFERENT_PERSON
            for outcome in confident
        ):
            if not context.mention.name_is_complete:
                # A surname alone or initials never create a canonical person (ADR 0012).
                return self._human("different_person_but_incomplete_name")
            return ReviewResolution(
                outcome=EntityReviewOutcome.AUTO_REJECTED,
                action=ResolutionReviewAction.CREATE_NEW_PERSON,
                reason=(
                    f"every candidate reviewed as different_person with confidence "
                    f">= {self._auto_threshold:.2f}"
                ),
            )
        uncertain = [
            outcome
            for outcome in outcomes
            if outcome.result.decision is EntityReviewDecision.UNCERTAIN
        ]
        if uncertain:
            return self._human("uncertain")
        return self._human(f"confidence_below_{self._auto_threshold:.2f}")

    def failure(self, reason: str) -> ReviewResolution:
        """A provider or validation failure: never a silent merge, always a human."""
        return ReviewResolution(outcome=EntityReviewOutcome.FAILED, reason=reason)

    @staticmethod
    def _human(reason: str) -> ReviewResolution:
        return ReviewResolution(outcome=EntityReviewOutcome.HUMAN_REQUIRED, reason=reason)
