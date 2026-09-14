"""Scoring, decision policy and alias promotion (pure, no database)."""

import pytest

from persons.resolution.aliases import AliasPromotionPolicy
from persons.resolution.decision import PersonResolutionDecisionPolicy, ResolutionThresholds
from persons.resolution.features import PersonResolutionFeatureExtractor
from persons.resolution.models import (
    PersonIdentityInput,
    PersonResolutionAction,
    PersonResolutionCandidate,
    PersonResolutionDecision,
    PersonResolutionReason,
    ScoredPersonCandidate,
    SemanticSourceStatus,
)
from persons.resolution.scoring import PersonResolutionScorer

extractor = PersonResolutionFeatureExtractor()
scorer = PersonResolutionScorer()
THRESHOLDS = ResolutionThresholds(auto_link_min_score=0.85, review_min_score=0.5, min_margin=0.1)
policy = PersonResolutionDecisionPolicy(THRESHOLDS)
A = PersonResolutionAction
R = PersonResolutionReason


def _scored(
    incoming: str,
    name: str,
    *,
    person_id: int = 1,
    aliases: list[str] | None = None,
    semantic: float | None = None,
) -> ScoredPersonCandidate:
    candidate = PersonResolutionCandidate(
        person_id=person_id,
        canonical_name=name,
        matching_key=f"key-{person_id}",
        aliases=aliases or [],
        semantic_similarity=semantic,
    )
    features = extractor.extract(PersonIdentityInput(name=incoming), candidate)
    return ScoredPersonCandidate(
        candidate=candidate, features=features, score=scorer.score(features)
    )


def _decide(
    incoming: str,
    *names: str,
    semantic: SemanticSourceStatus = SemanticSourceStatus.DISABLED,
) -> PersonResolutionDecision:
    candidates = [_scored(incoming, name, person_id=i) for i, name in enumerate(names, 1)]
    return policy.decide(PersonIdentityInput(name=incoming), candidates, semantic_source=semantic)


# --- scoring ---------------------------------------------------------------


def test_score_is_deterministic_for_equal_features() -> None:
    first = _scored("Иванов Иван Иванович", "Иван Иванович Иванов")
    second = _scored("Иванов Иван Иванович", "Иван Иванович Иванов")

    assert first.score == second.score


def test_score_orders_evidence_strength() -> None:
    exact = _scored("Иван Иванович Иванов", "Иван Иванович Иванов").resolution_score
    reordered = _scored("Иванов Иван Иванович", "Иван Иванович Иванов").resolution_score
    typo = _scored("Иван Иванович Пертров", "Иван Иванович Петров").resolution_score
    initials = _scored("И. И. Иванов", "Иван Иванович Иванов").resolution_score
    surname_only = _scored("Иванов", "Иван Иванов").resolution_score

    assert exact == 1.0
    assert exact > reordered > typo > initials > surname_only


def test_conflict_caps_the_score_instead_of_averaging() -> None:
    scored = _scored("Иван Петрович Иванов", "Иван Иванович Иванов")

    assert scored.resolution_score <= 0.25
    assert "conflict_cap" in scored.score.rules


def test_semantic_similarity_never_raises_the_score() -> None:
    plain = _scored("Пётр Сидоров", "Иван Иванов")
    semantic = _scored("Пётр Сидоров", "Иван Иванов", semantic=0.99)

    assert plain.resolution_score == semantic.resolution_score


# --- decision --------------------------------------------------------------


def test_no_candidates_create_new() -> None:
    decision = _decide("Иван Иванов")

    assert decision.action is A.CREATE_NEW
    assert decision.reasons == [R.NO_CANDIDATE]
    assert decision.selected_person_id is None


def test_strong_unique_candidate_auto_links() -> None:
    decision = _decide("Иван Иванович Иванов", "Иван Иванович Иванов", "Пётр Сидоров")

    assert decision.action is A.AUTO_LINK
    assert decision.selected_person_id == 1
    assert R.STRONG_UNIQUE_MATCH in decision.reasons


def test_two_equally_strong_candidates_review_as_possible_duplicates() -> None:
    decision = _decide("Иван Иванович Иванов", "Иван Иванович Иванов", "Иван Иванович Иванов")

    assert decision.action is A.REVIEW
    assert decision.selected_person_id is None
    assert decision.decision_margin == 0.0
    assert {
        R.LOW_DECISION_MARGIN,
        R.MULTIPLE_PLAUSIBLE_CANDIDATES,
        R.POSSIBLE_DUPLICATE_PERSONS,
    } <= set(decision.reasons)


def test_margin_below_minimum_reviews_even_with_high_top_score() -> None:
    # 1.0 vs 0.90 (reordered duplicate): margin 0.10 is not above the minimum.
    tight = PersonResolutionDecisionPolicy(
        ResolutionThresholds(auto_link_min_score=0.85, review_min_score=0.5, min_margin=0.15)
    )
    candidates = [
        _scored("Иван Иванович Иванов", "Иван Иванович Иванов", person_id=1),
        _scored("Иван Иванович Иванов", "Иванов Иван Иванович", person_id=2),
    ]
    decision = tight.decide(PersonIdentityInput(name="Иван Иванович Иванов"), candidates)

    assert decision.action is A.REVIEW
    assert R.LOW_DECISION_MARGIN in decision.reasons


def test_medium_candidate_reviews() -> None:
    decision = _decide("Иванов Иван", "Иван Иванов")

    assert decision.action is A.REVIEW
    assert R.MEDIUM_CONFIDENCE_MATCH in decision.reasons


def test_weak_candidates_create_new() -> None:
    decision = _decide("Пётр Сидоров", "Иван Иванов", "Анна Петрова")

    assert decision.action is A.CREATE_NEW
    assert R.NO_PLAUSIBLE_CANDIDATE in decision.reasons


def test_identity_conflict_never_auto_links_and_creates_new() -> None:
    decision = _decide("Иван Петрович Иванов", "Иван Иванович Иванов")

    assert decision.action is A.CREATE_NEW
    assert R.CONFLICTING_IDENTITY_DATA in decision.reasons


def test_ambiguous_initials_review() -> None:
    decision = _decide("И. Иванов", "Иван Иванов", "Илья Иванов", "Игорь Иванов")

    assert decision.action is A.REVIEW
    assert {R.INITIALS_ONLY, R.MULTIPLE_PLAUSIBLE_CANDIDATES} <= set(decision.reasons)


def test_single_initials_candidate_still_reviews() -> None:
    decision = _decide("И. Иванов", "Иван Иванов")

    assert decision.action is A.REVIEW
    assert R.INITIALS_ONLY in decision.reasons


def test_semantic_similarity_trap_does_not_auto_link() -> None:
    candidate = _scored("Пётр Сидоров", "Иван Иванов", semantic=0.97)
    decision = policy.decide(PersonIdentityInput(name="Пётр Сидоров"), [candidate])

    assert decision.action is not A.AUTO_LINK


def test_semantic_outage_keeps_a_safe_auto_link() -> None:
    decision = _decide(
        "Иван Иванович Иванов",
        "Иван Иванович Иванов",
        semantic=SemanticSourceStatus.UNAVAILABLE,
    )

    assert decision.action is A.AUTO_LINK


def test_semantic_outage_reviews_instead_of_create_new_when_ambiguous() -> None:
    # A same-surname candidate without conflict: only a surname in common.
    decision = _decide("Иванов", "Иван Иванов", semantic=SemanticSourceStatus.UNAVAILABLE)
    with_semantic = _decide("Пётр Сидоров", semantic=SemanticSourceStatus.UNAVAILABLE)

    assert decision.action is A.REVIEW
    # Nothing in common with any candidate: creating a person stays safe.
    assert with_semantic.action is A.CREATE_NEW


def test_semantic_outage_with_weak_surname_overlap_reviews() -> None:
    weak_policy = PersonResolutionDecisionPolicy(
        ResolutionThresholds(auto_link_min_score=0.85, review_min_score=0.6, min_margin=0.1)
    )
    candidates = [_scored("Иванов", "Иван Иванов")]
    outage = weak_policy.decide(
        PersonIdentityInput(name="Иванов"),
        candidates,
        semantic_source=SemanticSourceStatus.UNAVAILABLE,
    )
    healthy = weak_policy.decide(PersonIdentityInput(name="Иванов"), candidates)

    assert healthy.action is A.CREATE_NEW
    assert outage.action is A.REVIEW
    assert R.SEMANTIC_SOURCE_UNAVAILABLE in outage.reasons


def test_candidates_are_reported_in_score_order() -> None:
    decision = _decide("Иван Иванович Иванов", "Пётр Сидоров", "Иван Иванович Иванов")

    assert [c.person_id for c in decision.candidates] == [2, 1]


def test_thresholds_validate_ordering() -> None:
    with pytest.raises(ValueError):
        ResolutionThresholds(auto_link_min_score=0.4, review_min_score=0.5, min_margin=0.1)


def test_thresholds_from_env() -> None:
    thresholds = ResolutionThresholds.from_env(
        {"ER_AUTO_LINK_MIN_SCORE": "0.9", "ER_REVIEW_MIN_SCORE": "0.55", "ER_MIN_MARGIN": "0.2"}
    )

    assert thresholds == ResolutionThresholds(
        auto_link_min_score=0.9, review_min_score=0.55, min_margin=0.2
    )
    assert ResolutionThresholds.from_env({}) == ResolutionThresholds()


# --- alias promotion -------------------------------------------------------


def test_alias_promotion_only_for_clean_full_forms() -> None:
    promotion = AliasPromotionPolicy()

    assert promotion.should_promote(
        "Иванов Иван Иванович", _scored("Иванов Иван Иванович", "Иван Иванович Иванов").features
    )
    assert not promotion.should_promote(
        "И. И. Иванов", _scored("И. И. Иванов", "Иван Иванович Иванов").features
    )
    assert not promotion.should_promote(
        "Иван Иванович Пертров", _scored("Иван Иванович Пертров", "Иван Иванович Петров").features
    )
    assert not promotion.should_promote("Иванов", _scored("Иванов", "Иван Иванов").features)


LOW = PersonResolutionDecisionPolicy(
    ResolutionThresholds(auto_link_min_score=0.2, review_min_score=0.1, min_margin=0.0)
)


def test_initials_never_auto_link_even_with_lowered_thresholds() -> None:
    decision = LOW.decide(
        PersonIdentityInput(name="И. Иванов"), [_scored("И. Иванов", "Иван Иванов")]
    )

    assert decision.action is A.REVIEW
    assert R.INITIALS_ONLY in decision.reasons


def test_conflicts_are_never_plausible_even_with_lowered_thresholds() -> None:
    decision = LOW.decide(
        PersonIdentityInput(name="Иван Петрович Иванов"),
        [_scored("Иван Петрович Иванов", "Иван Иванович Иванов")],
    )

    assert decision.action is A.CREATE_NEW
    assert R.CONFLICTING_IDENTITY_DATA in decision.reasons


def test_two_strong_candidates_review_even_with_a_wide_margin() -> None:
    # 1.0 vs 0.90: margin 0.10 passes a 0.05 minimum, yet both are strong matches.
    wide = PersonResolutionDecisionPolicy(
        ResolutionThresholds(auto_link_min_score=0.85, review_min_score=0.4, min_margin=0.05)
    )
    candidates = [
        _scored("Иван Иванович Иванов", "Иван Иванович Иванов", person_id=1),
        _scored("Иван Иванович Иванов", "Иванов Иван Иванович", person_id=2),
    ]
    decision = wide.decide(PersonIdentityInput(name="Иван Иванович Иванов"), candidates)

    assert decision.action is A.REVIEW
    assert R.POSSIBLE_DUPLICATE_PERSONS in decision.reasons
    assert R.LOW_DECISION_MARGIN not in decision.reasons
