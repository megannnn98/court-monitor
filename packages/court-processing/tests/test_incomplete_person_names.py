"""Incomplete names (a surname or given name alone, a name with initials) never found a person.

End to end without a database: text -> RuleBasedEntityExtractor 1.7.1 -> RuleBasedMentionNormalizer ->
PersonResolutionDecisionPolicy (with the real feature extractor and scorer) -> entity grouping.
"""

import pytest

from monitor_core.model import ParsedArticle
from persons.resolution.ai_context import _name_is_complete
from persons.resolution.decision import PersonResolutionDecisionPolicy, ResolutionThresholds
from persons.resolution.features import PersonResolutionFeatureExtractor
from persons.resolution.models import (
    PersonIdentityInput,
    PersonResolutionAction,
    PersonResolutionCandidate,
    PersonResolutionDecision,
    PersonResolutionReason,
    ScoredPersonCandidate,
)
from persons.resolution.normalizer import PersonNameNormalizer, name_is_complete
from persons.resolution.scoring import PersonResolutionScorer
from processing.extraction.documents import build_extraction_document_from_parsed_article
from processing.extraction.extractors import RuleBasedEntityExtractor
from processing.extraction.models import EntityType, ExtractionDocument
from processing.extraction.normalizers import RuleBasedMentionNormalizer
from processing.identity.grouping import PersonMention, group_mentions

A = PersonResolutionAction
R = PersonResolutionReason
THRESHOLDS = ResolutionThresholds(auto_link_min_score=0.85, review_min_score=0.5, min_margin=0.1)
policy = PersonResolutionDecisionPolicy(THRESHOLDS)
extractor = PersonResolutionFeatureExtractor()
scorer = PersonResolutionScorer()
normalizer = RuleBasedMentionNormalizer()


def scored(incoming: str, name: str, person_id: int = 1) -> ScoredPersonCandidate:
    candidate = PersonResolutionCandidate(
        person_id=person_id, canonical_name=name, matching_key=f"key-{person_id}"
    )
    features = extractor.extract(PersonIdentityInput(name=incoming), candidate)
    return ScoredPersonCandidate(
        candidate=candidate, features=features, score=scorer.score(features)
    )


def decide(incoming: str, *candidates: str) -> PersonResolutionDecision:
    ranked = [scored(incoming, name, i) for i, name in enumerate(candidates, 1)]
    return policy.decide(PersonIdentityInput(name=incoming), ranked)


# --- one test of completeness ---------------------------------------------------------------


@pytest.mark.parametrize(
    "name,complete",
    [
        ("Иван Иванов", True),
        ("Иванов Иван Иванович", True),
        ("Иван И. Иванов", True),
        ("Мамед Гусейн оглы Алиев", True),
        ("Иванов И.И.", False),
        ("Иванов И. И.", False),
        ("Иванов И.", False),
        ("Иванов-Петров А.Б.", False),
        ("Виталия Л.", False),
        ("Бастрыкин А.И.", False),
        ("И. Иванов", False),
        ("Иванов", False),
        ("Ольга", False),
    ],
)
def test_a_name_is_complete_only_with_two_full_tokens(name: str, complete: bool) -> None:
    assert name_is_complete(name) is complete
    assert PersonNameNormalizer().normalize(name).has_full_identity is complete


def test_the_ai_reviewer_uses_the_same_test() -> None:
    """«А.И.» is one 3-character token for a split-based check and was read as a full name."""
    assert _name_is_complete("Бастрыкин А.И.") is False
    assert _name_is_complete("Александр Бастрыкин") is True


# --- the decision without a confirmed identity ----------------------------------------------


@pytest.mark.parametrize(
    "name,action",
    [
        ("Иван Иванов", A.CREATE_NEW),
        ("Иванов Иван Иванович", A.CREATE_NEW),
        ("Иванов И.И.", A.REVIEW),
        ("Иванов И. И.", A.REVIEW),
        ("Иванов И.", A.REVIEW),
        ("Виталия Л.", A.REVIEW),
        ("Бастрыкин А.И.", A.REVIEW),
        ("Иванов-Петров А.Б.", A.REVIEW),
        ("Иванов", A.REVIEW),
        ("Ольга", A.REVIEW),
    ],
)
def test_no_candidates(name: str, action: PersonResolutionAction) -> None:
    decision = policy.decide(PersonIdentityInput(name=name), [])
    assert decision.action is action
    if action is A.REVIEW:
        assert R.INCOMPLETE_NAME in decision.reasons
        assert decision.selected_person_id is None


def test_an_incomplete_name_with_initials_says_so() -> None:
    reasons = policy.decide(PersonIdentityInput(name="Бастрыкин А.И."), []).reasons
    assert {R.NO_CANDIDATE, R.INCOMPLETE_NAME, R.INITIALS_ONLY} <= set(reasons)


def test_no_plausible_candidate_does_not_create_a_person_from_initials() -> None:
    """Used to be CREATE_NEW: the candidates exist, but none of them is this surname."""
    decision = decide("Иванов И.И.", "Пётр Петров", "Анна Сидорова")
    assert decision.action is A.REVIEW
    assert R.INCOMPLETE_NAME in decision.reasons


def test_a_conflict_does_not_create_a_person_from_initials() -> None:
    """«Иванов И.П.» against «Иван Иванович Иванов»: a patronymic conflict, and used to be CREATE_NEW."""
    decision = decide("Иванов И.П.", "Иван Иванович Иванов")
    assert decision.action is A.REVIEW
    assert R.INCOMPLETE_NAME in decision.reasons


def test_one_candidate_with_the_surname_is_never_linked_by_initials() -> None:
    decision = decide("Иванов И.И.", "Иван Иванович Иванов")
    assert decision.action is A.REVIEW
    assert decision.selected_person_id is None
    assert R.INITIALS_ONLY in decision.reasons


def test_namesakes_with_the_same_initials_stay_in_review() -> None:
    decision = decide("Иванов И.И.", "Иван Иванов", "Илья Иванов", "Игорь Иванов")
    assert decision.action is A.REVIEW
    assert R.MULTIPLE_PLAUSIBLE_CANDIDATES in decision.reasons
    assert decision.selected_person_id is None


def test_two_people_with_the_same_initials_never_become_one_by_the_name() -> None:
    first = policy.decide(PersonIdentityInput(name="Иванов И.И.", article_id=1), [])
    second = policy.decide(PersonIdentityInput(name="Иванов И.И.", article_id=2), [])
    assert first.action is second.action is A.REVIEW
    assert first.selected_person_id is second.selected_person_id is None


def test_a_full_name_without_candidates_still_creates_a_person() -> None:
    decision = decide("Иван Иванов")
    assert decision.action is A.CREATE_NEW
    assert decision.reasons == [R.NO_CANDIDATE]


def test_a_full_name_with_a_confirmed_match_is_still_linked() -> None:
    decision = decide("Иванов Иван Иванович", "Иван Иванович Иванов")
    assert decision.action in {A.AUTO_LINK, A.REVIEW}
    assert decision.selected_person_id in {1, None}


def test_a_full_name_that_conflicts_still_creates_a_person() -> None:
    decision = decide("Иван Петрович Иванов", "Иван Иванович Иванов")
    assert decision.action is A.CREATE_NEW
    assert R.CONFLICTING_IDENTITY_DATA in decision.reasons


def test_a_bare_surname_repeated_in_the_article_still_links_to_the_named_person() -> None:
    candidate = scored("Иванов", "Иван Иванов")
    features = candidate.features.model_copy(update={"same_article_mention": True})
    decision = policy.decide(
        PersonIdentityInput(name="Иванов"), [candidate.model_copy(update={"features": features})]
    )
    assert decision.action is A.AUTO_LINK
    assert R.SAME_ARTICLE_SURNAME_REFERENCE in decision.reasons


# --- normalization of «Фамилия И.О.» --------------------------------------------------------


@pytest.mark.parametrize(
    "surface,normalized,last,first,patronymic",
    [
        ("Бастрыкин А.И.", "Бастрыкин А.И.", "Бастрыкин", "А.", "И."),
        ("Бастрыкин А. И.", "Бастрыкин А. И.", "Бастрыкин", "А.", "И."),
        ("Иванов И.И.", "Иванов И.И.", "Иванов", "И.", "И."),
        ("Иванов И. И.", "Иванов И. И.", "Иванов", "И.", "И."),
        ("Иванов И.", "Иванов И.", "Иванов", "И.", None),
        ("Иванов-Петров А.Б.", "Иванов-Петров А.Б.", "Иванов-Петров", "А.", "Б."),
        ("Малютиной К.С.", "Малютина К.С.", "Малютина", "К.", "С."),
        ("Азизову Б.Р.", "Азизова Б.Р.", "Азизова", "Б.", "Р."),
    ],
)
def test_surname_with_initials_is_split_into_the_right_fields(
    surface: str, normalized: str, last: str, first: str, patronymic: str | None
) -> None:
    text, data = normalizer.normalize_person(surface)
    assert text == normalized
    assert (data.last_name, data.first_name, data.patronymic) == (last, first, patronymic)
    assert data.full_name == normalized


def test_equivalent_writings_share_a_matching_key_and_different_people_do_not() -> None:
    def key(name: str) -> str:
        return normalizer.normalize_person(name)[1].matching_key

    assert key("Бастрыкин А.И.") == key("Бастрыкин А. И.")
    assert key("Малютиной К.С.") == key("Малютина К.С.")
    assert len({key("Иванов И.И."), key("Иванов П.П."), key("Иванов И."), key("Петров И.И.")}) == 4


def test_a_given_name_with_a_surname_initial_is_read_as_before() -> None:
    _, data = normalizer.normalize_person("Виталия Л.")
    assert (data.first_name, data.last_name, data.patronymic) == ("Виталия", "Л.", None)


def test_full_names_and_initials_first_are_normalized_as_before() -> None:
    _, full = normalizer.normalize_person("Иванов Иван Иванович")
    assert (full.last_name, full.first_name, full.patronymic) == ("Иванов", "Иван", "Иванович")
    _, plain = normalizer.normalize_person("Иван Иванов")
    assert (plain.last_name, plain.first_name, plain.patronymic) == ("Иванов", "Иван", None)
    text, initials_first = normalizer.normalize_person("Е.А. Аничкиной")
    assert text == "Е.А. Аничкина" and initials_first.last_name == "Аничкина"
    assert (
        initials_first.matching_key == normalizer.normalize_person("Е.А. Аничкина")[1].matching_key
    )


# --- the whole way: text -> extractor -> normalizer -> decision -> grouping ------------------


def document(text: str) -> ExtractionDocument:
    return build_extraction_document_from_parsed_article(
        article_id=1,
        article=ParsedArticle(
            external_id="1", url="https://t.me/test/1", title="Test", published_at=None, text=text
        ),
        source_name="telegram",
        source_url="https://t.me/test/1",
    )


def mentions_of(text: str) -> list[tuple[str, str, str | None, str | None, str | None, str]]:
    doc = document(text)
    out = []
    for mention in RuleBasedEntityExtractor().extract(doc):
        if mention.entity_type is not EntityType.PERSON:
            continue
        normalized = normalizer.normalize(mention, doc)
        data = normalized.normalized_data
        out.append(
            (
                mention.surface_text,
                normalized.normalized_text,
                data.last_name,  # type: ignore[union-attr]
                data.first_name,  # type: ignore[union-attr]
                data.patronymic,  # type: ignore[union-attr]
                data.matching_key,  # type: ignore[union-attr]
            )
        )
    return out


def test_the_whole_way_a_surname_with_initials_is_found_normalized_and_sent_to_review() -> None:
    [(surface, normalized, last, first, patronymic, key)] = mentions_of(
        "Председатель СК России Бастрыкин А.И. поручил проверить."
    )
    assert (surface, normalized) == ("Бастрыкин А.И.", "Бастрыкин А.И.")
    assert (last, first, patronymic) == ("Бастрыкин", "А.", "И.")
    identity = PersonIdentityInput(name=normalized, surface_text=surface, matching_key=key)
    assert policy.decide(identity, []).action is A.REVIEW
    assert (
        policy.decide(identity, [scored(normalized, "Александр Иванович Бастрыкин")]).action
        is A.REVIEW
    )


def test_the_whole_way_a_full_name_in_the_same_text_still_creates_a_person() -> None:
    found = mentions_of("Александр Бастрыкин и Иванов И.И. приехали.")
    by_surface = {surface: normalized for surface, normalized, *_ in found}
    assert set(by_surface) == {"Александр Бастрыкин", "Иванов И.И."}
    actions = {
        surface: policy.decide(PersonIdentityInput(name=normalized), []).action
        for surface, normalized in by_surface.items()
    }
    assert actions == {"Александр Бастрыкин": A.CREATE_NEW, "Иванов И.И.": A.REVIEW}


def test_the_whole_way_several_people_with_initials_stay_apart() -> None:
    found = mentions_of("Иванов И.И. и Иванов П.П. пришли, а Петров И.И. ушёл.")
    assert [n for _, n, *_ in found] == ["Иванов И.И.", "Иванов П.П.", "Петров И.И."]
    assert len({key for *_, key in found}) == 3
    for _, normalized, *_ in found:
        assert policy.decide(PersonIdentityInput(name=normalized), []).action is A.REVIEW


def test_grouping_attaches_a_surname_with_initials_to_the_named_person_of_the_article() -> None:
    """With the fields in the right places the mention is a reference, not a second «person»."""
    [full] = mentions_of("Александр Бастрыкин приехал.")
    [initials] = mentions_of("Бастрыкин А.И. поручил проверить.")
    mentions = [
        PersonMention(1, 1, full[3], full[2]),
        PersonMention(2, 1, initials[3], initials[2], initials[4]),
    ]
    entities = group_mentions(mentions)
    assert len(entities) == 1 and sorted(entities[0].mention_ids) == [1, 2]


def test_grouping_does_not_join_a_different_initial_or_another_article() -> None:
    mentions = [
        PersonMention(1, 1, "Александр", "Бастрыкин"),
        PersonMention(2, 1, "В.", "Бастрыкин", "И."),
        PersonMention(3, 2, "А.", "Бастрыкин", "И."),
    ]
    entities = group_mentions(mentions)
    joined = [sorted(e.mention_ids) for e in entities if 1 in e.mention_ids]
    assert joined == [[1]]
