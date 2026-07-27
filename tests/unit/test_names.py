"""Unit tests: name extraction heuristics."""

from __future__ import annotations

from court_monitor.extraction.names import extract_name_candidates


def test_full_fio_extraction():
    dtos = extract_name_candidates("Иванов Иван Иванович, 1983 года рождения")
    assert len(dtos) >= 1
    assert dtos[0].value == "Иванов Иван Иванович"
    assert dtos[0].confidence >= 0.9


def test_female_fio():
    dtos = extract_name_candidates("Иванова Анна Сергеевна признана виновной")
    assert len(dtos) >= 1
    assert dtos[0].value == "Иванова Анна Сергеевна"


def test_initials_with_surname():
    dtos = extract_name_candidates("Иванов И. И. задержан")
    assert len(dtos) >= 1
    assert dtos[0].value == "Иванов И. И."


def test_surname_with_initial():
    dtos = extract_name_candidates("Задержан Петров А. на месте происшествия")
    assert len(dtos) >= 1
    assert "Петров" in dtos[0].value


def test_not_court_name():
    """Court names should not be extracted as person names."""
    dtos = extract_name_candidates("Окружной военный суд рассмотрел дело")
    # Should not extract "Окружной военный" as a name
    values = [d.value for d in dtos]
    assert not any("Окружной" in v for v in values)


def test_not_organization():
    """Organization names should not be extracted."""
    dtos = extract_name_candidates("Следственного Комитета проведена проверка")
    values = [d.value for d in dtos]
    assert not any("Следственного" in v for v in values)


def test_not_position():
    """Position names should not be extracted."""
    dtos = extract_name_candidates("Председатель суда вынес определение")
    values = [d.value for d in dtos]
    assert not any("Председатель" in v for v in values)


def test_empty_text():
    assert extract_name_candidates("") == []


def test_no_duplicates():
    text = "Иванов Иван Иванович. Иванов Иван Иванович повторился."
    dtos = extract_name_candidates(text)
    values = [d.value for d in dtos]
    assert values.count("Иванов Иван Иванович") == 1


def test_quote_present():
    dtos = extract_name_candidates("Задержан Иванов Иван Иванович сотрудниками полиции")
    if dtos:
        assert dtos[0].quote
        assert "Иванов" in dtos[0].quote


def test_multiple_names():
    text = "Иванов Иван Иванович и Петрова Мария Сергеевна"
    dtos = extract_name_candidates(text)
    values = [d.value for d in dtos]
    assert len(values) >= 2
