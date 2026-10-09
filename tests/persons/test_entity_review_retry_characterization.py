"""Characterization of the ER review's retry loop (`_review_with_retries`).

`max_retries` counts retries, not attempts: max_retries=N makes at most N + 1 calls.
Only an `EntityReviewError` marked transient is retried, after 0.5 s, 1 s, 2 s, ...
between calls, never before the first. What comes out is the answer with the number
of calls, or `_ProviderFailure` carrying the last error object and the calls, with no
chained cause. Any other exception passes through on the first call.
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest
from sqlalchemy.orm import Session, sessionmaker

from persons.resolution.ai_policy import EntityReviewProvider, EntityReviewSettings
from persons.resolution.ai_review import (
    CandidateReviewContext,
    EntityReviewDecision,
    EntityReviewError,
    EntityReviewRequest,
    EntityReviewResult,
    EvidenceExcerpt,
    MentionReviewContext,
)
from persons.resolution.ai_review_service import AutomatedEntityReviewService, _ProviderFailure

RESULT = EntityReviewResult(
    decision=EntityReviewDecision.SAME_PERSON,
    confidence=0.95,
    supporting_evidence=["the same case"],
    explanation="because",
)
REQUEST = EntityReviewRequest(
    decision_id=7,
    mention=MentionReviewContext(
        mention_id=3,
        name="Иван Иванов",
        evidence=(EvidenceExcerpt(article_id=1, text="Суд арестовал Ивана Иванова"),),
    ),
    candidate=CandidateReviewContext(
        person_id=42,
        canonical_name="Иван Иванов",
        matching_key="иванов иван",
        resolution_score=0.8,
        surname_match="exact",
        given_name_match="exact",
        patronymic_match="missing",
    ),
    deterministic_score=0.8,
    matched_features=("surname:exact",),
)


def transient(n: int) -> EntityReviewError:
    return EntityReviewError(f"unreachable {n}", transient=True)


class Scripted:
    def __init__(self, outcomes: Sequence[EntityReviewResult | Exception]) -> None:
        self._outcomes = list(outcomes)
        self.calls = 0

    def review(self, request: EntityReviewRequest) -> EntityReviewResult:
        outcome = self._outcomes[self.calls]
        self.calls += 1
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _service(
    outcomes: Sequence[EntityReviewResult | Exception], *, max_retries: int
) -> tuple[AutomatedEntityReviewService, Scripted, list[float]]:
    reviewer = Scripted(outcomes)
    sleeps: list[float] = []
    service = AutomatedEntityReviewService(
        sessionmaker[Session](),
        reviewer,
        settings=EntityReviewSettings(
            provider=EntityReviewProvider.TOGETHER,
            model="m",
            max_retries=max_retries,
            prompt_version="v1",
        ),
        model="m",
        sleep=sleeps.append,
    )
    return service, reviewer, sleeps


def test_a_first_answer_is_one_call_and_no_sleep() -> None:
    service, reviewer, sleeps = _service([RESULT], max_retries=3)

    assert service._review_with_retries(REQUEST) == (RESULT, 1)
    assert (reviewer.calls, sleeps) == (1, [])


@pytest.mark.parametrize(
    ("failures", "sleeps"),
    [(1, [0.5]), (2, [0.5, 1.0]), (3, [0.5, 1.0, 2.0])],
)
def test_transient_failures_are_retried_with_doubling_delays(
    failures: int, sleeps: list[float]
) -> None:
    service, reviewer, slept = _service(
        [*(transient(n) for n in range(failures)), RESULT], max_retries=3
    )

    assert service._review_with_retries(REQUEST) == (RESULT, failures + 1)
    assert reviewer.calls == failures + 1
    assert slept == sleeps


@pytest.mark.parametrize(
    ("max_retries", "calls", "sleeps"),
    [(0, 1, []), (1, 2, [0.5]), (2, 3, [0.5, 1.0]), (3, 4, [0.5, 1.0, 2.0])],
)
def test_max_retries_counts_retries_and_the_last_error_comes_out(
    max_retries: int, calls: int, sleeps: list[float]
) -> None:
    errors = [transient(n) for n in range(calls + 1)]
    service, reviewer, slept = _service(errors, max_retries=max_retries)

    with pytest.raises(_ProviderFailure) as caught:
        service._review_with_retries(REQUEST)

    assert reviewer.calls == calls
    assert slept == sleeps
    assert caught.value.error is errors[calls - 1]
    assert caught.value.provider_calls == calls
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__ is True


def test_a_final_error_is_not_retried() -> None:
    final = EntityReviewError("does not match the contract", transient=False)
    service, reviewer, sleeps = _service([transient(0), final, RESULT], max_retries=3)

    with pytest.raises(_ProviderFailure) as caught:
        service._review_with_retries(REQUEST)

    assert caught.value.error is final
    assert (caught.value.provider_calls, reviewer.calls, sleeps) == (2, 2, [0.5])


def test_another_exception_passes_through_unretried() -> None:
    boom = RuntimeError("bug")
    service, reviewer, sleeps = _service([boom, RESULT], max_retries=3)

    with pytest.raises(RuntimeError) as caught:
        service._review_with_retries(REQUEST)

    assert caught.value is boom
    assert (reviewer.calls, sleeps) == (1, [])


def test_an_interrupted_wait_carries_the_failure_it_waited_after() -> None:
    class Interrupted(BaseException):
        pass

    def interrupt(seconds: float) -> None:
        raise Interrupted

    failure = transient(0)
    service, reviewer, _ = _service([failure, RESULT], max_retries=3)
    service._sleep = interrupt

    with pytest.raises(Interrupted) as caught:
        service._review_with_retries(REQUEST)

    assert caught.value.__context__ is failure
    assert reviewer.calls == 1


def test_a_review_error_from_the_wait_is_not_taken_for_the_provider_s() -> None:
    # The wait runs after the failed call, not inside it: what the wait raises leaves as
    # it is, even an `EntityReviewError`, and is never reported as a provider failure.
    interrupted = EntityReviewError("the wait broke", transient=False)

    def interrupt(seconds: float) -> None:
        raise interrupted

    failure = transient(0)
    service, reviewer, _ = _service([failure, RESULT], max_retries=3)
    service._sleep = interrupt

    with pytest.raises(EntityReviewError) as caught:
        service._review_with_retries(REQUEST)

    assert caught.value is interrupted
    assert caught.value.__context__ is failure
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__ is False
    assert reviewer.calls == 1
