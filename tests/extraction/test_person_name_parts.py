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


def test_parts_of_edge_inputs() -> None:
    assert name_parts([]) == (None, None, None)
    # More than three words: the first and the last.
    assert name_parts(["Мамед", "Гусейн", "оглы", "Алиев"]) == ("Мамед", "Алиев", None)
