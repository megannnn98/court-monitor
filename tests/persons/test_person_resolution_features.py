from persons.resolution.features import PersonResolutionFeatureExtractor
from persons.resolution.models import (
    ComponentMatch,
    IdentityConflict,
    PersonIdentityInput,
    PersonResolutionCandidate,
    PersonResolutionFeatures,
)

extractor = PersonResolutionFeatureExtractor()


def _candidate(
    name: str, *, aliases: list[str] | None = None, semantic: float | None = None
) -> PersonResolutionCandidate:
    return PersonResolutionCandidate(
        person_id=1,
        canonical_name=name,
        matching_key="".join(name.lower().split()),
        aliases=aliases or [],
        semantic_similarity=semantic,
    )


def _features(
    incoming: str, candidate: PersonResolutionCandidate, *, key: str | None = None
) -> PersonResolutionFeatures:
    return extractor.extract(PersonIdentityInput(name=incoming, matching_key=key), candidate)


def test_identical_full_names_are_exact_in_every_component() -> None:
    features = _features("Иван Иванович Иванов", _candidate("Иван Иванович Иванов"))

    assert features.exact_name
    assert (features.surname, features.given_name, features.patronymic) == (
        ComponentMatch.EXACT,
        ComponentMatch.EXACT,
        ComponentMatch.EXACT,
    )
    assert not features.order_differs
    assert features.conflicts == []


def test_reordered_name_aligns_components_and_flags_order() -> None:
    features = _features("Иванов Иван Иванович", _candidate("Иван Иванович Иванов"))

    assert not features.exact_name
    assert features.surname is ComponentMatch.EXACT
    assert features.given_name is ComponentMatch.EXACT
    assert features.patronymic is ComponentMatch.EXACT
    assert features.order_differs


def test_yo_and_case_do_not_break_exactness() -> None:
    features = _features("СЕМЁН Алёшин", _candidate("Семен Алешин"))

    assert features.exact_name
    assert features.conflicts == []


def test_different_patronymic_is_an_explicit_conflict() -> None:
    features = _features("Иван Петрович Иванов", _candidate("Иван Иванович Иванов"))

    assert features.surname is ComponentMatch.EXACT
    assert features.given_name is ComponentMatch.EXACT
    assert features.patronymic is ComponentMatch.MISMATCH
    assert features.conflicts == [IdentityConflict.PATRONYMIC_MISMATCH]


def test_same_surname_different_given_name_conflicts() -> None:
    features = _features("Илья Иванов", _candidate("Иван Иванов"))

    assert features.surname is ComponentMatch.EXACT
    assert IdentityConflict.GIVEN_NAME_MISMATCH in features.conflicts


def test_compatible_initials_are_not_exact_and_mark_initials_only() -> None:
    features = _features("И. И. Иванов", _candidate("Иван Иванович Иванов"))

    assert features.surname is ComponentMatch.EXACT
    assert features.given_name is ComponentMatch.INITIAL_COMPATIBLE
    assert features.patronymic is ComponentMatch.INITIAL_COMPATIBLE
    assert features.initials_only
    assert features.conflicts == []


def test_incompatible_initial_conflicts() -> None:
    features = _features("П. Иванов", _candidate("Иван Иванов"))

    assert features.given_name is ComponentMatch.MISMATCH
    assert IdentityConflict.GIVEN_NAME_MISMATCH in features.conflicts


def test_one_character_typo_in_surname() -> None:
    features = _features("Александр Пертров", _candidate("Александр Петров"))

    assert features.surname is ComponentMatch.TYPO
    assert features.given_name is ComponentMatch.EXACT
    assert features.surname_similarity is not None and 0.8 < features.surname_similarity < 1
    assert features.conflicts == []


def test_short_names_get_no_typo_tolerance() -> None:
    # "Иван" / "Иона": one edit apart but different names.
    features = _features("Иона Петров", _candidate("Иван Петров"))

    assert features.given_name is ComponentMatch.MISMATCH


def test_known_alias_is_compared_and_reported() -> None:
    candidate = _candidate("Иван Иванович Иванов", aliases=["Иванов Ваня"])
    features = _features("Ваня Иванов", candidate)

    assert features.compared_form_is_alias
    assert features.given_name is ComponentMatch.EXACT
    assert features.conflicts == []


def test_exact_alias_form() -> None:
    features = _features("Ваня Иванов", _candidate("Иван Иванов", aliases=["Ваня Иванов"]))

    assert features.exact_alias
    assert not features.exact_name


def test_surname_only_is_incomplete_without_given_name_match() -> None:
    features = _features("Иванов", _candidate("Иван Иванов"))

    assert features.incomplete_name
    assert features.surname is ComponentMatch.EXACT
    assert features.given_name is ComponentMatch.MISSING


def test_semantic_similarity_is_carried_as_diagnostics() -> None:
    features = _features("Пётр Сидоров", _candidate("Иван Иванов", semantic=0.91))

    assert features.semantic_similarity == 0.91
    assert features.conflicts


def test_exact_matching_key_feature() -> None:
    features = _features("Иван Иванов", _candidate("Иван Иванов"), key="иваниванов")

    assert features.exact_matching_key


def test_extraction_is_deterministic() -> None:
    candidate = _candidate("Иван Иванович Иванов", aliases=["И. И. Иванов"])

    assert _features("Иванов Иван", candidate) == _features("Иванов Иван", candidate)


def test_implausible_reading_does_not_hide_a_conflict() -> None:
    # Reading "Иванов" as a given name on both sides would avoid the conflict.
    features = _features("Иван Иванов", _candidate("Иванов Пётр"))

    assert features.surname is ComponentMatch.EXACT
    assert IdentityConflict.GIVEN_NAME_MISMATCH in features.conflicts


def test_missing_component_is_not_a_reordering() -> None:
    assert not _features("Иванов", _candidate("Иван Иванов")).order_differs
    assert not _features("Иван Иванов", _candidate("Иван Иванович Иванов")).order_differs


def test_dropped_letter_in_a_short_surname_is_a_typo() -> None:
    assert _features("Джон Смитт", _candidate("Джон Смит")).surname is ComponentMatch.TYPO
