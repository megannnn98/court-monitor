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
