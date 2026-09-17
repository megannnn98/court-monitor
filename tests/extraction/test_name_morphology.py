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


def test_the_words_of_a_name_are_read_in_one_gender() -> None:
    """«Даниила» may be a woman's name; the unknown «Неонова» was declined as a man's.

    Real corpus: «Даниила Неонов» half-declined matched neither «Даниил Неонов» nor
    anything else, and none of his ten mentions reached his person.
    """
    assert MORPHOLOGY.to_nominative("Даниила Неонова") == "Даниил Неонов"
    assert MORPHOLOGY.to_nominative("Станислава Зимина") == "Станислав Зимин"
    # Nothing was declined: the name stays as written, as one reading.
    assert MORPHOLOGY.to_nominative("Александра Новака") == "Александра Новака"


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


def test_the_mention_itself_in_the_article_does_not_block_the_gender() -> None:
    """Real case: the pipeline passes every capitalized word of the article, including the
    ambiguous «Телина» itself, which read as both genders and cancelled «Телин»."""
    article = ["Дело", "Федора", "Телина", "Телин", "Россию"]
    assert MORPHOLOGY.to_nominative("Федора Телина", document_words=article) == "Фёдор Телин"
    article = ["Суд", "Даниила", "Меркулова", "Меркулов"]
    assert MORPHOLOGY.to_nominative("Даниила Меркулова", article) == "Даниил Меркулов"
    # A woman: «Волковой» settles it, the ambiguous «Волкову» does not get in the way.
    article = ["Ольгу", "Волкову", "Волковой"]
    assert MORPHOLOGY.to_nominative("Ольгу Волкову", article) == "Ольга Волкова"
    article = ["Юлию", "Молчанову", "Молчановой"]
    assert MORPHOLOGY.to_nominative("Юлию Молчанову", article) == "Юлия Молчанова"


def test_a_guessed_reading_does_not_fix_the_gender() -> None:
    """Real case: «Лидии Мониавы» became «Лидий»: the dictionary does not know «Мониавы»
    and its guessed masculine reading settled the name."""
    assert MORPHOLOGY.to_nominative("Лидии Мониавы").split()[0] == "Лидия"
    # Real case: «Айшат» is a masculine name in the dictionary; the surname says otherwise.
    assert MORPHOLOGY.to_nominative("Айшат Кадыровой") == "Айшат Кадырова"


@pytest.mark.parametrize(
    ("surface", "expected"),
    [
        # Real cases: feminine surnames outside the dictionary kept their case ending.
        ("Елене Перепелице", "Елена Перепелица"),
        ("Юлию Таратуту", "Юлия Таратута"),
        ("Лилию Хвыльку", "Лилия Хвылька"),
        ("Лидии Мониавы", "Лидия Мониава"),
        # Real case «Антона Зарецкого»; «Зарецк» and «Зарецка» were invented stems.
        ("Антона Зарецкого", "Антон Зарецкий"),
        ("Антону Зарецкому", "Антон Зарецкий"),
        ("Антоном Зарецким", "Антон Зарецкий"),
        ("Анне Зарецкой", "Анна Зарецкая"),
        ("Анну Зарецкую", "Анна Зарецкая"),
        # Indeclinable: a woman's surname that does not end in a case ending stays.
        ("Ксению Сиваконь", "Ксения Сиваконь"),
        ("Анастасию Гордиенко", "Анастасия Гордиенко"),
        ("Екатерину Котрикадзе", "Екатерина Котрикадзе"),
        # «Постового» may be «Постовой» or «Постовый».
        ("Олега Постового", "Олег Постового"),
    ],
)
def test_a_surname_outside_the_dictionary_is_declined_once_the_gender_is_known(
    surface: str, expected: str
) -> None:
    assert MORPHOLOGY.to_nominative(surface) == expected


def test_ambiguous_names_are_settled_by_the_weight_of_their_readings() -> None:
    """Real cases found by re-normalizing the stored mentions."""
    assert MORPHOLOGY.to_nominative("Вячеславу Авдашеву") == "Вячеслав Авдашев"
    assert MORPHOLOGY.to_nominative("Владлена Татарского") == "Владлен Татарский"
    assert MORPHOLOGY.to_nominative("Юлию Емельянову") == "Юлия Емельянова"


def test_a_womans_surname_ending_in_a_consonant_is_indeclinable() -> None:
    """Real case: «Марии Бонцлер» was stored as «Мария Бонцлера»."""
    assert MORPHOLOGY.to_nominative("Марии Бонцлер") == "Мария Бонцлер"
    assert MORPHOLOGY.to_nominative("Айшат Кадыровой") == "Айшат Кадырова"
    # «-м» is an instrumental ending, not a consonant-final surname.
    assert MORPHOLOGY.to_nominative("Набиюллой Балабековым") != "Набиюлла Балабековым"
