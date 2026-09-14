from persons.resolution.models import NameRole, NameVariant, RoleSignal
from persons.resolution.normalizer import PersonNameNormalizer

normalizer = PersonNameNormalizer()
S, G, P = NameRole.SURNAME, NameRole.GIVEN_NAME, NameRole.PATRONYMIC


def _readings(text: str) -> list[dict[NameRole, str]]:
    return [_reading(variant) for variant in normalizer.normalize(text).variants]


def _reading(variant: NameVariant) -> dict[NameRole, str]:
    return {part.role: part.text for part in variant.components}


def test_case_whitespace_and_yo_produce_the_same_canonical_form() -> None:
    forms = {
        normalizer.normalize(text).canonical_form
        for text in (
            "ИВАНОВ Иван Иванович",
            "иванов иван иванович",
            "  Иванов  Иван   Иванович ",
            "Иванов Иван Иванович",
        )
    }

    assert forms == {"иванов иван иванович"}
    assert normalizer.normalize("Семён Алёшин").canonical_form == "семен алешин"


def test_reordered_full_name_has_a_common_role_reading() -> None:
    expected = {S: "иванов", G: "иван", P: "иванович"}

    assert _readings("Иванов Иван Иванович")[0] == expected
    assert _readings("Иван Иванович Иванов")[0] == expected


def test_two_word_names_keep_both_orders_instead_of_guessing() -> None:
    readings = _readings("Иван Иванов")

    assert {G: "иван", S: "иванов"} in readings
    assert {S: "иван", G: "иванов"} in readings
    # Surname suffix only ranks the reading first; it does not remove the other.
    assert readings[0] == {G: "иван", S: "иванов"}


def test_foreign_surname_without_suffixes_is_still_parsed() -> None:
    readings = _readings("Джон Смит")

    assert {G: "джон", S: "смит"} in readings
    assert {S: "джон", G: "смит"} in readings


def test_patronymic_suffix_is_a_hint_not_a_rule() -> None:
    # "Шостакович" is a surname despite the patronymic-like ending.
    readings = _readings("Дмитрий Шостакович")

    assert {G: "дмитрий", S: "шостакович"} in readings
    variant = next(
        v
        for v in normalizer.normalize("Дмитрий Шостакович").variants
        if v.component(S) and v.component(S).text == "шостакович"  # type: ignore[union-attr]
    )
    assert RoleSignal.PATRONYMIC_SUFFIX_OUTSIDE_PATRONYMIC in variant.signals


def test_initials_before_or_after_surname() -> None:
    for text in ("И. И. Иванов", "Иванов И. И.", "И.И.Иванов", "Иванов И.И."):
        name = normalizer.normalize(text)
        (variant,) = name.variants
        assert name.has_initials
        assert _reading(variant) == {G: "и", P: "и", S: "иванов"}
        assert all(part.is_initial for part in variant.components if part.role is not S)

    (single,) = normalizer.normalize("И. Иванов").variants
    assert _reading(single) == {G: "и", S: "иванов"}


def test_single_token_is_incomplete_with_both_roles() -> None:
    name = normalizer.normalize("Иванов")

    assert name.is_incomplete
    assert [_reading(v) for v in name.variants] == [{S: "иванов"}, {G: "иванов"}]


def test_unparseable_names_keep_tokens_but_no_variants() -> None:
    name = normalizer.normalize("Мария Анна Иванова Петрова")

    assert name.variants == ()
    assert name.canonical_form == "мария анна иванова петрова"


def test_hyphenated_surname_is_one_token() -> None:
    assert _readings("Иванов-Петров Иван")[0] == {S: "иванов-петров", G: "иван"}


def test_block_keys_are_identical_for_reordered_forms() -> None:
    forms = ("Иван Иванов", "Иванов Иван", "Иван Иванович Иванов", "Иванов Иван Иванович")
    keys = {text: normalizer.normalize(text).block_keys for text in forms}

    assert keys["Иван Иванов"] == keys["Иванов Иван"] == ("иван", "иванов")
    assert keys["Иван Иванович Иванов"] == keys["Иванов Иван Иванович"]
    assert keys["Иван Иванович Иванов"] == ("иван", "иванов", "иванович")
    # Short and initial forms overlap with the full form on the surname.
    assert "иванов" in normalizer.normalize("И. Иванов").block_keys


def test_normalization_is_deterministic() -> None:
    assert normalizer.normalize("Иванов Иван") == normalizer.normalize("Иванов Иван")
