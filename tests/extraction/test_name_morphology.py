"""Nominative form of a personal name (real cases from the loaded corpus)."""

import pytest

from extraction.name_morphology import NameMorphology

MORPHOLOGY = NameMorphology()


@pytest.mark.parametrize(
    ("surface", "expected"),
    [
        # Genitive / dative / accusative / instrumental of a full name.
        ("Михаила Лисина", "Михаил Лисин"),
        ("Антону Ермакову", "Антон Ермаков"),
        ("Андреем Яриным", "Андрей Ярин"),
        ("Алексею Навальному", "Алексей Навальный"),
        # A feminine name must not be read as a masculine one: «Юлий Емельянов» is another person.
        ("Юлию Емельянову", "Юлия Емельянова"),
        ("Ольгу Комлеву", "Ольга Комлева"),
        ("Дарьи Буньковой", "Дарья Бунькова"),
        ("Диане Камильяновой", "Диана Камильянова"),
        ("Зареме Мусаевой", "Зарема Мусаева"),
        # Patronymics keep their own ending.
        ("Шевченко Татьяну Андреевну", "Шевченко Татьяна Андреевна"),
        # Already nominative: unchanged.
        ("Игорь Краснов", "Игорь Краснов"),
        ("Карина Ким", "Карина Ким"),
        ("Мария Львова-Белова", "Мария Львова-Белова"),
    ],
)
def test_name_is_brought_to_the_nominative_case(surface: str, expected: str) -> None:
    assert MORPHOLOGY.to_nominative(surface) == expected


def test_a_surname_the_dictionary_does_not_know_is_left_as_written() -> None:
    """«Мампорию» must not become an invented «Мампорий»: a wrong name is worse than a declined one."""
    assert MORPHOLOGY.to_nominative("Мампорию") == "Мампорию"


def test_the_same_surface_always_gives_the_same_result() -> None:
    assert [MORPHOLOGY.to_nominative("Ольгу Комлеву") for _ in range(3)] == ["Ольга Комлева"] * 3


def test_an_ambiguous_word_is_left_as_written_when_the_name_states_no_gender() -> None:
    """«Волкова» is a woman in the nominative or a man in the genitive; initials say nothing.

    Real-world validation: guessing by dictionary frequency made «Волкова О. Н.» a man and
    lowered every ER metric (auto-link recall 0.804 → 0.797, candidate recall@5 0.967 → 0.961).
    """
    assert MORPHOLOGY.to_nominative("Волкова О. Н.") == "Волкова О. Н."
    assert MORPHOLOGY.to_nominative("Волкова") == "Волкова"
    assert MORPHOLOGY.to_nominative("Александра Иванова") == "Александра Иванова"


def test_one_unambiguous_word_settles_the_gender_of_the_whole_name() -> None:
    """«Андрея» is masculine in every reading, so the surname follows it."""
    assert MORPHOLOGY.to_nominative("Андрея Волкова") == "Андрей Волков"
    assert MORPHOLOGY.to_nominative("Ольгу Волкову") == "Ольга Волкова"


def test_words_are_classified_as_name_parts_or_ordinary_words() -> None:
    """Real cases: «России Мария Захарова», «Российской Федерации» as a person."""
    assert MORPHOLOGY.is_name_word("Мария") and MORPHOLOGY.is_name_word("Захарова")
    assert MORPHOLOGY.is_name_word("Роман")  # both a name and a common noun
    assert not MORPHOLOGY.is_name_word("России")

    assert MORPHOLOGY.is_known_non_name("Федерации")
    assert MORPHOLOGY.is_known_non_name("России")
    assert not MORPHOLOGY.is_known_non_name("Мария")
    # Unknown to the dictionary: a foreign surname, not something to drop.
    assert not MORPHOLOGY.is_known_non_name("Тирни")
    assert not MORPHOLOGY.is_known_non_name("Росавиации")


def test_the_article_settles_an_ambiguous_gender() -> None:
    """Real case: «Федора Телина» stayed declined; the same article writes «Телин»."""
    assert MORPHOLOGY.to_nominative("Федора Телина") == "Федора Телина"
    assert MORPHOLOGY.to_nominative("Федора Телина", document_words=["Телин"]) == "Фёдор Телин"
    # The article says the surname is a woman's: «Волкова» stays a woman.
    assert MORPHOLOGY.to_nominative("Волкова", document_words=["Волковой"]) == "Волкова"


def test_a_patronymic_with_a_rare_surname_reading_is_still_a_patronymic() -> None:
    """The dictionary also reads «Александрович» as an indeclinable feminine surname."""
    assert MORPHOLOGY.is_patronymic("Александрович")
    assert MORPHOLOGY.is_patronymic("Сергеевна")
    assert not MORPHOLOGY.is_patronymic("Иванова")
    assert not MORPHOLOGY.is_patronymic("Мария")
