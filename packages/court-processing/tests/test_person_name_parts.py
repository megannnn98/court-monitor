"""The parts of a person's name as the normalizer stores them. Entity grouping reads
these three fields; `full_name` and `matching_key` are checked not to move."""

from __future__ import annotations

import pytest

from processing.extraction.normalizers import RuleBasedMentionNormalizer, name_parts

NORMALIZER = RuleBasedMentionNormalizer()


@pytest.mark.parametrize(
    ("surface", "parts"),
    [
        # Three words: the patronymic tells the given name from the surname. Before
        # 1.7.0 all of these stored the first word as both given name and surname.
        ("Анна Викторовна Новикова", ("Анна", "Новикова", "Викторовна")),
        ("Новикова Анна Викторовна", ("Анна", "Новикова", "Викторовна")),
        ("Ивана Петровича Сидорова", ("Иван", "Сидоров", "Петрович")),
        ("Сидоров Иван Ильич", ("Иван", "Сидоров", "Ильич")),
        # Two words, either order.
        ("Иван Сидоров", ("Иван", "Сидоров", None)),
        ("Сидоров Иван", ("Иван", "Сидоров", None)),
        # Neither word a known given name, or both: the order is kept as written.
        ("Дрион Алексис", ("Дрион", "Алексис", None)),
        ("Роман Борис", ("Роман", "Борис", None)),
        ("Сидоров", (None, "Сидоров", None)),
    ],
)
def test_the_parts_of_a_name(
    surface: str, parts: tuple[str | None, str | None, str | None]
) -> None:
    _, data = NORMALIZER.normalize_person(surface)

    assert (data.first_name, data.last_name, data.patronymic) == parts


@pytest.mark.parametrize(
    "surface",
    [
        # A span that took the surname of the person named before it: «адвоката
        # Ивановой Петра Сидорова», «сын Иванова Вячеслав Сидоров».
        "Ивановой Петра Сидорова",
        "Иванов Вячеслав Сидоров",
    ],
)
def test_a_span_with_a_neighbours_surname_is_not_named_by_that_surname(surface: str) -> None:
    _, data = NORMALIZER.normalize_person(surface)

    assert data.patronymic is None
    assert data.first_name != data.last_name
    assert (data.last_name or "").startswith("Сидоров")
    assert not (data.first_name or "").startswith("Иванов")


@pytest.mark.parametrize(
    ("surface", "full_name", "matching_key"),
    [
        ("Анна Викторовна Новикова", "Анна Викторовна Новикова", "аннавикторовнановикова"),
        ("Новикова Анна Викторовна", "Новикова Анна Викторовна", "новиковааннавикторовна"),
        ("Сидоров Иван", "Сидоров Иван", "сидоровиван"),
    ],
)
def test_the_full_name_and_the_matching_key_stay_as_written(
    surface: str, full_name: str, matching_key: str
) -> None:
    _, data = NORMALIZER.normalize_person(surface)

    assert (data.full_name, data.matching_key) == (full_name, matching_key)


@pytest.mark.parametrize(
    ("words", "parts"),
    [
        # A name of several parts keeps every word: the middle one tells two people apart.
        (["Джон", "Пол", "Смит"], ("Джон", "Смит", "Пол")),
        (["Джеймс", "Пол", "Смит"], ("Джеймс", "Смит", "Пол")),
        (["Джон", "Давид", "Смит"], ("Джон", "Смит", "Давид")),
        (["Джеймс", "Давид", "Смит"], ("Джеймс", "Смит", "Давид")),
        (["Анна", "Мария", "Смит"], ("Анна", "Смит", "Мария")),
        (["Анна", "Елена", "Смит"], ("Анна", "Смит", "Елена")),
        (["Мамед", "Гусейн", "оглы", "Алиев"], ("Мамед", "Алиев", "Гусейн оглы")),
        (["Жан", "Клод", "Ван", "Дамм"], ("Жан", "Дамм", "Клод Ван")),
        # A registry's order, also when the surname is a given name too.
        (["Герман", "Иван", "Петрович"], ("Иван", "Герман", "Петрович")),
        (["Герман", "Олег", "Петрович"], ("Олег", "Герман", "Петрович")),
        (["Вагнер", "Иван", "Ильич"], ("Иван", "Вагнер", "Ильич")),
        (["Сидоров", "Авксентий", "Ильич"], ("Авксентий", "Сидоров", "Ильич")),
        (["Иван", "Петрович", "Шостакович"], ("Иван", "Шостакович", "Петрович")),
        (["Шостакович", "Дмитрий", "Дмитриевич"], ("Дмитрий", "Шостакович", "Дмитриевич")),
        # A foreign «Имя Фамилия» whose surname is a Russian given name is not turned.
        (["Джон", "Герман"], ("Джон", "Герман", None)),
        (["Шевчук", "Александр"], ("Александр", "Шевчук", None)),
        # A neighbour's surname, then a given name the dictionary left declined.
        (["Иванова", "Романа", "Сидорова"], ("Романа", "Сидорова", None)),
        # No Russian surname in front: nothing says the first word is a neighbour's.
        (["Вагнера", "Вячеслав", "Сидоров"], ("Вагнера", "Сидоров", "Вячеслав")),
    ],
)
def test_parts_keep_every_word_that_tells_a_name_apart(
    words: list[str], parts: tuple[str | None, str | None, str | None]
) -> None:
    assert name_parts(words) == parts


@pytest.mark.parametrize(
    ("one", "other"),
    [
        (["Джон", "Давид", "Смит"], ["Джеймс", "Давид", "Смит"]),
        (["Анна", "Мария", "Смит"], ["Анна", "Елена", "Смит"]),
        (["Герман", "Иван", "Петрович"], ["Герман", "Олег", "Петрович"]),
    ],
)
def test_two_people_never_get_the_same_parts(one: list[str], other: list[str]) -> None:
    assert name_parts(one) != name_parts(other)


def test_what_the_words_alone_do_not_tell() -> None:
    # Known limits, kept visible. Every word is kept, so nobody is merged by them: the
    # worst they do is keep one person apart from their own shorter name.
    assert name_parts(["Иван", "Давид", "Абрамович"]) == ("Давид", "Иван", "Абрамович")
    assert name_parts(["Жан", "Давид", "Абрамович"]) == ("Давид", "Жан", "Абрамович")
    assert name_parts(["Роман", "Иван"]) == ("Роман", "Иван", None)


def test_parts_of_edge_inputs() -> None:
    assert name_parts([]) == (None, None, None)


@pytest.mark.parametrize(
    ("surface", "parts"),
    [
        ("Бастрыкин А.И.", ("А.", "Бастрыкин", "И.")),
        ("Бастрыкин А. И.", ("А.", "Бастрыкин", "И.")),
        ("Иванов И.И.", ("И.", "Иванов", "И.")),
        ("Иванов И. И.", ("И.", "Иванов", "И.")),
        ("Иванов-Петров А.Б.", ("А.", "Иванов-Петров", "Б.")),
        ("Бастрыкин А.", ("А.", "Бастрыкин", None)),
    ],
)
def test_surname_followed_by_initials(
    surface: str, parts: tuple[str | None, str | None, str | None]
) -> None:
    _, data = NORMALIZER.normalize_person(surface)
    assert (data.first_name, data.last_name, data.patronymic) == parts


def test_surname_initials_matching_key_ignores_spaces() -> None:
    compact, data_compact = NORMALIZER.normalize_person("Бастрыкин А.И.")
    spaced, data_spaced = NORMALIZER.normalize_person("Бастрыкин А. И.")

    assert compact == "Бастрыкин А.И."
    assert spaced == "Бастрыкин А. И."
    assert data_compact.matching_key == data_spaced.matching_key == "бастрыкинаи"


@pytest.mark.parametrize(
    "words",
    [
        ["Виталия", "Л."],
        ["Мартин", "А.И."],
    ],
)
def test_known_given_name_before_initial_remains_ambiguous(words: list[str]) -> None:
    # A single initial is insufficient evidence to reinterpret a known given name
    # as a surname. This deliberately leaves some ambiguous surnames unresolved.
    assert name_parts(words) == (words[0], words[1], None)
