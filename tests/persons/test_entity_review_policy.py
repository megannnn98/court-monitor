"""The deterministic policy over AI answers: what may be applied automatically."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from persons.resolution.ai_policy import (
    CandidateReviewOutcome,
    EntityReviewConfigurationError,
    EntityReviewOutcome,
    EntityReviewPolicy,
    EntityReviewProvider,
    EntityReviewSettings,
)
from persons.resolution.ai_review import (
    CandidateReviewContext,
    DecisionReviewContext,
    EntityReviewDecision,
    EntityReviewRequest,
    EntityReviewResult,
    EvidenceExcerpt,
    MentionReviewContext,
)
from persons.resolution.models import PersonResolutionReason
from persons.resolution.review import ResolutionReviewAction

CASES_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "entity_review_cases.json"


def mention(name: str = "Иван Иванов", *, complete: bool = True) -> MentionReviewContext:
    return MentionReviewContext(mention_id=1, name=name, name_is_complete=complete)


def candidate(
    person_id: int = 10,
    *,
    conflicts: tuple[str, ...] = (),
    score: float = 0.8,
) -> CandidateReviewContext:
    return CandidateReviewContext(
        person_id=person_id,
        canonical_name="Иван Иванов",
        matching_key="иванов иван",
        resolution_score=score,
        surname_match="exact",
        given_name_match="exact",
        patronymic_match="missing",
        conflicts=conflicts,
    )


def outcome(
    decision: EntityReviewDecision,
    confidence: float,
    *,
    person_id: int = 10,
    conflicting_features: tuple[str, ...] = (),
    name_complete: bool = True,
) -> CandidateReviewOutcome:
    return CandidateReviewOutcome(
        request=EntityReviewRequest(
            decision_id=1,
            mention=mention(complete=name_complete),
            candidate=candidate(person_id),
            deterministic_score=0.8,
            conflicting_features=conflicting_features,
        ),
        result=EntityReviewResult(decision=decision, confidence=confidence),
    )


def context(
    outcomes: list[CandidateReviewOutcome],
    *,
    reasons: tuple[PersonResolutionReason, ...] = (),
    name_complete: bool = True,
) -> DecisionReviewContext:
    return DecisionReviewContext(
        decision_id=1,
        mention=mention(complete=name_complete),
        requests=tuple(item.request for item in outcomes),
        review_reasons=reasons,
    )


# --- the rules ----------------------------------------------------------------------


def test_a_confident_same_person_links_the_mention() -> None:
    outcomes = [outcome(EntityReviewDecision.SAME_PERSON, 0.95)]

    resolution = EntityReviewPolicy().resolve(context(outcomes), outcomes)

    assert resolution.outcome is EntityReviewOutcome.AUTO_ACCEPTED
    assert resolution.action is ResolutionReviewAction.LINK_TO_PERSON
    assert resolution.person_id == 10


def test_every_candidate_rejected_creates_a_new_person() -> None:
    outcomes = [
        outcome(EntityReviewDecision.DIFFERENT_PERSON, 0.93, person_id=10),
        outcome(EntityReviewDecision.DIFFERENT_PERSON, 0.91, person_id=11),
    ]

    resolution = EntityReviewPolicy().resolve(context(outcomes), outcomes)

    assert resolution.outcome is EntityReviewOutcome.AUTO_REJECTED
    assert resolution.action is ResolutionReviewAction.CREATE_NEW_PERSON
    assert resolution.person_id is None


def test_uncertain_goes_to_a_human() -> None:
    outcomes = [outcome(EntityReviewDecision.UNCERTAIN, 0.99)]

    resolution = EntityReviewPolicy().resolve(context(outcomes), outcomes)

    assert resolution.outcome is EntityReviewOutcome.HUMAN_REQUIRED
    assert resolution.action is None
    assert resolution.reason == "uncertain"


def test_confidence_at_the_threshold_is_enough_and_below_it_is_not() -> None:
    policy = EntityReviewPolicy(auto_threshold=0.9)
    at = [outcome(EntityReviewDecision.SAME_PERSON, 0.9)]
    below = [outcome(EntityReviewDecision.SAME_PERSON, 0.8999)]

    assert policy.resolve(context(at), at).outcome is EntityReviewOutcome.AUTO_ACCEPTED
    resolution = policy.resolve(context(below), below)
    assert resolution.outcome is EntityReviewOutcome.HUMAN_REQUIRED
    assert resolution.reason == "confidence_below_0.90"


def test_a_configured_threshold_replaces_the_default() -> None:
    outcomes = [outcome(EntityReviewDecision.SAME_PERSON, 0.95)]

    strict = EntityReviewPolicy(auto_threshold=0.99).resolve(context(outcomes), outcomes)

    assert strict.outcome is EntityReviewOutcome.HUMAN_REQUIRED


def test_conflicting_identity_data_forbids_an_automatic_link() -> None:
    outcomes = [
        outcome(
            EntityReviewDecision.SAME_PERSON,
            0.99,
            conflicting_features=("patronymic_mismatch",),
        )
    ]

    resolution = EntityReviewPolicy().resolve(context(outcomes), outcomes)

    assert resolution.outcome is EntityReviewOutcome.HUMAN_REQUIRED
    assert "patronymic_mismatch" in resolution.reason


@pytest.mark.parametrize(
    "reason",
    [
        PersonResolutionReason.MULTIPLE_EXACT_NAME_MATCHES,
        PersonResolutionReason.POSSIBLE_DUPLICATE_PERSONS,
        PersonResolutionReason.KNOWN_DISTINCT_PERSONS,
    ],
)
def test_namesakes_and_possible_duplicates_are_never_linked_automatically(
    reason: PersonResolutionReason,
) -> None:
    outcomes = [outcome(EntityReviewDecision.SAME_PERSON, 0.99)]

    resolution = EntityReviewPolicy().resolve(context(outcomes, reasons=(reason,)), outcomes)

    assert resolution.outcome is EntityReviewOutcome.HUMAN_REQUIRED
    assert reason.value in resolution.reason


def test_two_candidates_reviewed_as_the_same_person_go_to_a_human() -> None:
    outcomes = [
        outcome(EntityReviewDecision.SAME_PERSON, 0.95, person_id=10),
        outcome(EntityReviewDecision.SAME_PERSON, 0.93, person_id=11),
    ]

    resolution = EntityReviewPolicy().resolve(context(outcomes), outcomes)

    assert resolution.outcome is EntityReviewOutcome.HUMAN_REQUIRED
    assert resolution.reason == "several_candidates_reviewed_as_same_person"


def test_an_incomplete_name_is_never_turned_into_a_new_person() -> None:
    outcomes = [outcome(EntityReviewDecision.DIFFERENT_PERSON, 0.97, name_complete=False)]

    resolution = EntityReviewPolicy().resolve(context(outcomes, name_complete=False), outcomes)

    assert resolution.outcome is EntityReviewOutcome.HUMAN_REQUIRED
    assert resolution.reason == "different_person_but_incomplete_name"


def test_a_partly_rejected_decision_stays_with_a_human() -> None:
    outcomes = [
        outcome(EntityReviewDecision.DIFFERENT_PERSON, 0.95, person_id=10),
        outcome(EntityReviewDecision.DIFFERENT_PERSON, 0.4, person_id=11),
    ]

    resolution = EntityReviewPolicy().resolve(context(outcomes), outcomes)

    assert resolution.outcome is EntityReviewOutcome.HUMAN_REQUIRED


def test_nothing_to_review_is_a_human_decision() -> None:
    resolution = EntityReviewPolicy().resolve(context([]), [])

    assert resolution.outcome is EntityReviewOutcome.HUMAN_REQUIRED
    assert resolution.reason == "no_candidate_to_review"


def test_a_failure_never_applies_anything() -> None:
    resolution = EntityReviewPolicy().failure("timeout")

    assert resolution.outcome is EntityReviewOutcome.FAILED
    assert resolution.action is None
    assert resolution.applies is False


def test_evidence_excerpts_are_trimmed_to_a_quote() -> None:
    long_text = "а" * 5_000

    trimmed = EvidenceExcerpt(text=long_text).trimmed()

    assert len(trimmed.text) < len(long_text)
    assert trimmed.text.endswith("…")
    assert EvidenceExcerpt(text="короткий").trimmed().text == "короткий"


def test_the_input_hash_changes_with_the_data_but_not_with_the_decision_id() -> None:
    outcomes = [outcome(EntityReviewDecision.SAME_PERSON, 0.95)]
    first = context(outcomes)
    same_input = first.model_copy(update={"decision_id": 999})
    other_input = first.model_copy(update={"mention": mention("Пётр Петров")})

    assert first.input_hash() == same_input.input_hash()
    assert first.input_hash() != other_input.input_hash()


# --- settings -----------------------------------------------------------------------


def test_settings_default_to_no_reviewer_and_the_safe_threshold() -> None:
    settings = EntityReviewSettings.from_env({})

    assert settings.provider is EntityReviewProvider.NONE
    assert settings.enabled is False
    assert settings.auto_threshold == 0.90
    assert settings.max_retries == 3
    assert settings.prompt_version == "v1"


def test_settings_read_the_environment_and_reject_bad_values() -> None:
    settings = EntityReviewSettings.from_env(
        {
            "ENTITY_REVIEW_PROVIDER": "together",
            "ENTITY_REVIEW_MODEL": "some/model",
            "ENTITY_REVIEW_AUTO_THRESHOLD": "0.95",
            "ENTITY_REVIEW_TIMEOUT_SECONDS": "12",
            "ENTITY_REVIEW_MAX_RETRIES": "1",
            "ENTITY_REVIEW_PROMPT_VERSION": "v2",
        }
    )

    assert settings.enabled is True
    assert (settings.model, settings.auto_threshold, settings.max_retries) == (
        "some/model",
        0.95,
        1,
    )
    assert settings.prompt_version == "v2"
    # A blank value is the default, as everywhere else in the configuration.
    assert (
        EntityReviewSettings.from_env({"ENTITY_REVIEW_PROMPT_VERSION": " "}).prompt_version == "v1"
    )
    for env in (
        {"ENTITY_REVIEW_PROVIDER": "openai"},
        {"ENTITY_REVIEW_AUTO_THRESHOLD": "0.1"},
        {"ENTITY_REVIEW_AUTO_THRESHOLD": "high"},
        {"ENTITY_REVIEW_TIMEOUT_SECONDS": "0"},
        {"ENTITY_REVIEW_MAX_RETRIES": "-1"},
        {"ENTITY_REVIEW_MAX_RETRIES": "many"},
    ):
        with pytest.raises(EntityReviewConfigurationError):
            EntityReviewSettings.from_env(env)


# --- the labelled corpus ------------------------------------------------------------


def _case_outcome(case: dict[str, Any]) -> tuple[DecisionReviewContext, CandidateReviewOutcome]:
    raw_candidate = case["candidate"]
    mention_context = MentionReviewContext(
        mention_id=1,
        name=case["mention"]["name"],
        name_is_complete=case["mention"]["name_is_complete"],
    )
    candidate_context = CandidateReviewContext(
        person_id=10,
        canonical_name=raw_candidate["canonical_name"],
        matching_key=raw_candidate["canonical_name"].lower(),
        aliases=tuple(raw_candidate.get("aliases", ())),
        resolution_score=raw_candidate["resolution_score"],
        surname_match=raw_candidate["surname_match"],
        given_name_match=raw_candidate["given_name_match"],
        patronymic_match=raw_candidate["patronymic_match"],
        conflicts=tuple(raw_candidate.get("conflicts", ())),
        same_article_mention=raw_candidate.get("same_article_mention", False),
        case_context_match=raw_candidate.get("case_context_match", False),
        evidence=tuple(EvidenceExcerpt(text=text) for text in raw_candidate.get("evidence", ())),
    )
    request = EntityReviewRequest(
        decision_id=1,
        mention=mention_context,
        candidate=candidate_context,
        deterministic_score=raw_candidate["resolution_score"],
        conflicting_features=candidate_context.conflicts,
        review_reasons=tuple(PersonResolutionReason(name) for name in case["review_reasons"]),
    )
    review_outcome = CandidateReviewOutcome(
        request=request,
        result=EntityReviewResult(
            decision=EntityReviewDecision(case["ai"]["decision"]),
            confidence=case["ai"]["confidence"],
        ),
    )
    review_context = DecisionReviewContext(
        decision_id=1,
        mention=mention_context,
        requests=(request,),
        review_reasons=request.review_reasons,
    )
    return review_context, review_outcome


def test_the_labelled_pairs_resolve_as_the_fixture_says() -> None:
    cases = json.loads(CASES_PATH.read_text("utf-8"))["cases"]
    policy = EntityReviewPolicy()
    assert len(cases) >= 15

    results: dict[str, tuple[str, str | None]] = {}
    for case in cases:
        review_context, review_outcome = _case_outcome(case)
        resolution = policy.resolve(review_context, [review_outcome])
        results[case["case_id"]] = (
            resolution.outcome.value,
            None if resolution.action is None else resolution.action.value,
        )

    expected = {
        case["case_id"]: (case["expected_outcome"], case.get("expected_action")) for case in cases
    }
    assert results == expected
    # The corpus must exercise all three outcomes, not only the easy one.
    assert {outcome for outcome, _ in expected.values()} == {
        "auto_accepted",
        "auto_rejected",
        "human_required",
    }
