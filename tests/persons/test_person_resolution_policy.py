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

    # A surname alone never creates a person (real-world validation v1).
    assert healthy.action is A.REVIEW
    assert R.INCOMPLETE_NAME in healthy.reasons
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


def _keyed(incoming: str, name: str, *, person_id: int, key: str) -> ScoredPersonCandidate:
    candidate = PersonResolutionCandidate(
        person_id=person_id, canonical_name=name, matching_key=key
    )
    features = extractor.extract(PersonIdentityInput(name=incoming, matching_key=key), candidate)
    return ScoredPersonCandidate(
        candidate=candidate, features=features, score=scorer.score(features)
    )


# 1.0 vs 0.90 with a 0.05 minimum margin and a single strong (>= 0.95) candidate:
# only the shared matching_key is left to block the link.
ONE_STRONG = PersonResolutionDecisionPolicy(
    ResolutionThresholds(auto_link_min_score=0.95, review_min_score=0.4, min_margin=0.05)
)


def test_several_persons_with_the_incoming_key_review_whatever_the_scores() -> None:
    candidates = [
        _keyed("Иван Иванович Иванов", "Иван Иванович Иванов", person_id=1, key="k"),
        _keyed("Иван Иванович Иванов", "Иванов Иван Иванович", person_id=2, key="k"),
    ]
    decision = ONE_STRONG.decide(
        PersonIdentityInput(name="Иван Иванович Иванов", matching_key="k"), candidates
    )

    assert decision.action is A.REVIEW and decision.selected_person_id is None
    assert decision.reasons == [R.MULTIPLE_PLAUSIBLE_CANDIDATES, R.MULTIPLE_EXACT_NAME_MATCHES]


def test_single_exact_key_candidate_auto_links() -> None:
    decision = ONE_STRONG.decide(
        PersonIdentityInput(name="Иван Иванович Иванов", matching_key="k"),
        [_keyed("Иван Иванович Иванов", "Иван Иванович Иванов", person_id=1, key="k")],
    )

    assert (decision.action, decision.selected_person_id) == (A.AUTO_LINK, 1)


def test_known_distinct_strong_candidates_still_review() -> None:
    wide = PersonResolutionDecisionPolicy(
        ResolutionThresholds(auto_link_min_score=0.85, review_min_score=0.4, min_margin=0.05)
    )
    candidates = [
        _scored("Иван Иванович Иванов", "Иван Иванович Иванов", person_id=1),
        _scored("Иван Иванович Иванов", "Иванов Иван Иванович", person_id=2),
    ]
    identity = PersonIdentityInput(name="Иван Иванович Иванов")

    known = wide.decide(identity, candidates, distinct_pairs={frozenset((1, 2))})
    unrelated = wide.decide(identity, candidates, distinct_pairs={frozenset((1, 3))})

    assert known.action is A.REVIEW
    assert R.KNOWN_DISTINCT_PERSONS in known.reasons
    assert R.POSSIBLE_DUPLICATE_PERSONS not in known.reasons
    assert R.POSSIBLE_DUPLICATE_PERSONS in unrelated.reasons


def test_exact_complete_form_is_strong_whatever_reading_wins() -> None:
    # A suffix hint reads "Шостакович" as a patronymic; a 4-token name has no reading.
    suffix_trap = _scored("Дмитрий Шостакович", "Дмитрий Шостакович")
    unparsed = _scored("Мамед Гусейн оглы Алиев", "Мамед Гусейн оглы Алиев")
    unparsed_other = _scored("Мамед Гусейн оглы Алиев", "Мамед Гусейн оглы Алиева")

    assert suffix_trap.resolution_score == unparsed.resolution_score == 0.85
    assert "exact_complete_form" in unparsed.score.rules
    assert unparsed_other.resolution_score < THRESHOLDS.review_min_score


def test_exact_form_floor_does_not_cover_incomplete_or_initials_names() -> None:
    assert _scored("Иванов", "Иванов").resolution_score < 0.85
    assert _scored("И. Иванов", "И. Иванов").resolution_score < 0.85


# --- name-only evidence (real-world validation v1, namesake benchmark) -------------------


def _in_article(scored: ScoredPersonCandidate, same_article: bool) -> ScoredPersonCandidate:
    features = scored.features.model_copy(update={"same_article_mention": same_article})
    return scored.model_copy(update={"features": features})


@pytest.mark.parametrize("incoming", ["Иван Фролов", "Николай Маркин", "Андрея Шабанова"])
def test_name_without_patronymic_from_another_article_is_not_identity_evidence(
    incoming: str,
) -> None:
    """Real cases ns-30/ns-31: a different journalist «Иван Фролов» and a different
    «Николай Маркин» were AUTO_LINKed to the only existing person with that name
    (score 0.85, `exact_complete_form`, patronymic missing). The same name alone
    must not link across articles."""
    existing = incoming if incoming != "Андрея Шабанова" else "Андрей Шабанов"
    candidate = _in_article(_scored(incoming, existing), same_article=False)

    decision = policy.decide(PersonIdentityInput(name=incoming), [candidate])

    assert decision.action is A.REVIEW
    assert R.NAME_ONLY_EVIDENCE in decision.reasons
    assert decision.selected_person_id is None


def test_repeated_name_in_the_same_article_still_auto_links() -> None:
    candidate = _in_article(_scored("Иван Фролов", "Иван Фролов"), same_article=True)

    decision = policy.decide(PersonIdentityInput(name="Иван Фролов"), [candidate])

    assert (decision.action, decision.selected_person_id) == (A.AUTO_LINK, 1)


def test_full_name_with_matching_patronymic_auto_links_across_articles() -> None:
    candidate = _in_article(
        _scored("Мифтахов Азат Фанисович", "Азат Фанисович Мифтахов"), same_article=False
    )

    decision = policy.decide(PersonIdentityInput(name="Мифтахов Азат Фанисович"), [candidate])

    assert decision.action is A.AUTO_LINK
    assert R.NAME_ONLY_EVIDENCE not in decision.reasons


def test_given_name_and_patronymic_reading_is_still_name_only() -> None:
    # «Дмитрий Шостакович» may read as given name + patronymic: no surname, no identity proof.
    candidate = _in_article(_scored("Дмитрий Шостакович", "Дмитрий Шостакович"), same_article=False)

    decision = policy.decide(PersonIdentityInput(name="Дмитрий Шостакович"), [candidate])

    assert decision.action is A.REVIEW


def test_surname_alone_links_to_the_only_person_of_the_article_with_that_surname() -> None:
    """«Зареме Мусаевой… Мусаеву признали виновной»: the surname repeats the person named
    in full earlier in the same article."""
    candidate = _in_article(_scored("Мусаеву", "Зарема Мусаева"), same_article=True)

    decision = policy.decide(PersonIdentityInput(name="Мусаеву"), [candidate])

    assert (decision.action, decision.selected_person_id) == (A.AUTO_LINK, 1)
    assert R.SAME_ARTICLE_SURNAME_REFERENCE in decision.reasons


def test_surname_alone_with_two_persons_of_that_surname_in_the_article_reviews() -> None:
    """Real case: father and son Гилманов are both named in one article."""
    son = _in_article(_scored("Гилманова", "Марат Гилманов", person_id=1), same_article=True)
    father = _in_article(_scored("Гилманова", "Радик Гилманов", person_id=2), same_article=True)

    decision = policy.decide(PersonIdentityInput(name="Гилманова"), [son, father])

    assert decision.action is A.REVIEW


def test_surname_alone_never_links_to_a_person_from_another_article() -> None:
    candidate = _in_article(_scored("Мусаеву", "Зарема Мусаева"), same_article=False)

    decision = policy.decide(PersonIdentityInput(name="Мусаеву"), [candidate])

    assert decision.action is not A.AUTO_LINK


@pytest.mark.parametrize("with_candidate", [False, True])
def test_surname_alone_never_creates_a_person(with_candidate: bool) -> None:
    """Real cases: «По словам Фроловой…» and «Мифтаховым» created persons «Фроловая» and
    «Мифтаховый» when the full name of the article was pending review; later mentions then
    linked to that duplicate."""
    candidates = (
        [_in_article(_scored("Фроловой", "Иван Фролов"), same_article=False)]
        if with_candidate
        else []
    )

    decision = policy.decide(PersonIdentityInput(name="Фроловой"), candidates)

    assert decision.action is A.REVIEW
    assert R.INCOMPLETE_NAME in decision.reasons


def _in_case_context(scored: ScoredPersonCandidate, context: bool) -> ScoredPersonCandidate:
    features = scored.features.model_copy(update={"case_context_match": context})
    return scored.model_copy(update={"features": features})


def test_name_only_match_in_a_persecution_case_context_auto_links() -> None:
    """Amendment 2026-09-17: the incoming mention is the target of a persecution event and
    the candidate is named in news of the last half year. Measured on the stored queue: 30
    of 30 sampled links were the same person («Светлана Савельева», 5 articles)."""
    candidate = _in_case_context(_scored("Светлана Савельева", "Светлана Савельева"), True)

    decision = policy.decide(PersonIdentityInput(name="Светлана Савельева"), [candidate])

    assert (decision.action, decision.selected_person_id) == (A.AUTO_LINK, 1)
    assert R.CASE_CONTEXT_MATCH in decision.reasons
    assert R.NAME_ONLY_EVIDENCE not in decision.reasons


def test_case_context_does_not_lift_the_other_blocks() -> None:
    namesakes = [
        _in_case_context(_scored("Иван Фролов", "Иван Фролов", person_id=i), True) for i in (1, 2)
    ]
    namesakes = [
        item.model_copy(
            update={"features": item.features.model_copy(update={"exact_matching_key": True})}
        )
        for item in namesakes
    ]
    decision = policy.decide(PersonIdentityInput(name="Иван Фролов"), namesakes)
    assert decision.action is A.REVIEW
    assert R.MULTIPLE_EXACT_NAME_MATCHES in decision.reasons

    initials = _in_case_context(_scored("И. Фролов", "Иван Фролов"), True)
    decision = policy.decide(PersonIdentityInput(name="И. Фролов"), [initials])
    assert decision.action is A.REVIEW


def test_without_case_context_a_name_only_match_is_still_reviewed() -> None:
    candidate = _in_case_context(_scored("Иван Фролов", "Иван Фролов"), False)

    decision = policy.decide(PersonIdentityInput(name="Иван Фролов"), [candidate])

    assert decision.action is A.REVIEW
    assert R.NAME_ONLY_EVIDENCE in decision.reasons
