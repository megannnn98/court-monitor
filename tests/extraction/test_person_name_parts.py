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
        # A name of several parts keeps its two ends: two such people stay apart.
        (["Джон", "Пол", "Смит"], ("Джон", "Смит", None)),
        (["Джеймс", "Пол", "Смит"], ("Джеймс", "Смит", None)),
        (["Анна", "Мария", "Смит"], ("Анна", "Смит", None)),
        # A surname in «-вич» after two given names is not a patronymic.
        (["Иван", "Давид", "Абрамович"], ("Иван", "Абрамович", None)),
        (["Иван", "Петрович", "Шостакович"], ("Иван", "Шостакович", "Петрович")),
        (["Шостакович", "Дмитрий", "Дмитриевич"], ("Дмитрий", "Шостакович", "Дмитриевич")),
        # A registry's order with a surname of no Russian ending.
        (["Вагнер", "Иван", "Ильич"], ("Иван", "Вагнер", "Ильич")),
        # A foreign «Имя Фамилия» whose surname is a Russian given name is not turned.
        (["Джон", "Герман"], ("Джон", "Герман", None)),
        (["Шевчук", "Александр"], ("Александр", "Шевчук", None)),
        # A neighbour's surname, then a given name the dictionary left declined.
        (["Иванова", "Романа", "Сидорова"], ("Романа", "Сидорова", None)),
        (["Мамед", "Гусейн", "оглы", "Алиев"], ("Мамед", "Алиев", "Гусейн оглы")),
        (["Жан", "Клод", "Ван", "Дамм"], ("Жан", "Дамм", None)),
    ],
)
def test_parts_of_names_the_first_rule_got_wrong(
    words: list[str], parts: tuple[str | None, str | None, str | None]
) -> None:
    assert name_parts(words) == parts


def test_what_the_words_alone_do_not_tell() -> None:
    # Known limits, kept visible: a given name the lists do not hold, before a surname
    # in «-вич»; and two given names in a row.
    assert name_parts(["Жан", "Давид", "Абрамович"]) == ("Давид", "Жан", "Абрамович")
    assert name_parts(["Роман", "Иван"]) == ("Роман", "Иван", None)


def test_parts_of_edge_inputs() -> None:
    assert name_parts([]) == (None, None, None)
