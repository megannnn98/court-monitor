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


def test_not_foreign_agent_disclaimer():
    """Real production example: the legally mandated ALL-CAPS foreign-agent
    disclaimer must not be extracted as a person, but a genuine name
    elsewhere in the same text still must be found."""
    text = (
        "Ростовчане призвали ускорить работы. "
        "НАСТОЯЩИЙ МАТЕРИАЛ (ИНФОРМАЦИЯ) ПРОИЗВЕДЕН И РАСПРОСТРАНЕН "
        'ИНОСТРАННЫМ АГЕНТОМ ООО "МЕМО", ЛИБО КАСАЕТСЯ ДЕЯТЕЛЬНОСТИ '
        'ИНОСТРАННОГО АГЕНТА ООО "МЕМО". '
        "По словам губернатора, ситуацию прокомментировал Юрий Слюсарь."
    )
    values = [d.value for d in extract_name_candidates(text)]
    assert not any("ИНОСТРАН" in v.upper() and v.isupper() for v in values)
    assert any("Слюсарь" in v for v in values)


def test_not_all_caps_abbreviation():
    """A bare multi-letter ALL-CAPS run (agency abbreviation) is not a name."""
    dtos = extract_name_candidates("СБУ квалифицировала произошедшее как теракт")
    assert dtos == []


def test_not_hyphenated_outlet_or_topic_abbreviation():
    """An ALL-CAPS abbreviation hyphenated with a real word (media outlet,
    "РБК-Украина", or a topic label, "ЛГБТ-активистки") is not a name —
    checked, unlike a genuine hyphenated double-barrelled surname below."""
    dtos = extract_name_candidates('"РБК-Украина" сообщило о происшествии')
    assert not any("РБК" in d.value for d in dtos)

    dtos2 = extract_name_candidates("Против ЛГБТ-активистки возбудили дело")
    assert not any("ЛГБТ" in d.value for d in dtos2)


def test_hyphenated_surname_still_extracted():
    """A genuine double-barrelled surname (both parts Title Case) must not
    be rejected by the ALL-CAPS filter."""
    dtos = extract_name_candidates("Иванов-Петров Иван Иванович задержан")
    assert any(d.value == "Иванов-Петров Иван Иванович" for d in dtos)


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
