"""Tests for Rosfinmonitoring parser."""

from rosfinmonitoring.parser import (
    CsvRosfinmonitoringParser,
    JsonRosfinmonitoringParser,
    XmlRosfinmonitoringParser,
    _create_matching_key,
    _normalize_name,
)


def test_normalize_name_simple() -> None:
    """Test simple name normalization."""
    assert _normalize_name("Иванов Иван Иванович") == "иванов иван иванович"


def test_normalize_name_with_yo() -> None:
    """Test normalization with ё → е."""
    assert _normalize_name("Ёлкин Ёлка Ёлкович") == "елкин елка елкович"


def test_normalize_name_with_hyphens() -> None:
    """Test normalization with hyphens."""
    assert _normalize_name("Иванов-Петров Иван") == "иванов петров иван"


def test_normalize_name_with_dots() -> None:
    """Test normalization with dots."""
    assert _normalize_name("Иванов И.И.") == "иванов и и"


def test_create_matching_key() -> None:
    """Test matching key creation."""
    normalized = "иванов иван иванович"
    assert _create_matching_key(normalized) == "ивановиваниванович"


def test_csv_parser_basic() -> None:
    """Test CSV parser with basic data."""
    csv_content = "full_name,birth_date,inclusion_reason\nИванов Иван Иванович,01.01.1980,Тестовая причина\nПетров Петр Петрович,15.05.1975,Другая причина".encode()

    parser = CsvRosfinmonitoringParser()
    entries = parser.parse(csv_content)

    assert len(entries) == 2
    assert entries[0].full_name == "Иванов Иван Иванович"
    assert entries[0].normalized_name == "иванов иван иванович"
    assert entries[0].matching_key == "ивановиваниванович"
    assert entries[0].birth_date is not None
    assert entries[0].birth_date.year == 1980
    assert entries[0].inclusion_reason == "Тестовая причина"

    assert entries[1].full_name == "Петров Петр Петрович"
    assert entries[1].birth_date is not None
    assert entries[1].birth_date.year == 1975


def test_csv_parser_with_russian_headers() -> None:
    """Test CSV parser with Russian headers."""
    csv_content = (
        "ФИО,Дата рождения,Причина включения\nСидоров Сидор Сидорович,20.03.1990,Тест".encode()
    )

    parser = CsvRosfinmonitoringParser()
    entries = parser.parse(csv_content)

    assert len(entries) == 1
    assert entries[0].full_name == "Сидоров Сидор Сидорович"


def test_json_parser_basic() -> None:
    """Test JSON parser with basic data."""
    json_content = """[
        {
            "full_name": "Иванов Иван Иванович",
            "birth_date": "1980-01-01",
            "inclusion_reason": "Тест"
        },
        {
            "full_name": "Петров Петр Петрович",
            "birth_date": "1975-05-15"
        }
    ]""".encode()

    parser = JsonRosfinmonitoringParser()
    entries = parser.parse(json_content)

    assert len(entries) == 2
    assert entries[0].full_name == "Иванов Иван Иванович"
    assert entries[0].birth_date is not None
    assert entries[0].birth_date.year == 1980
    assert entries[1].full_name == "Петров Петр Петрович"


def test_json_parser_with_wrapper() -> None:
    """Test JSON parser with wrapper object."""
    json_content = """{
        "entries": [
            {
                "full_name": "Иванов Иван Иванович",
                "birth_date": "01.01.1980"
            }
        ]
    }""".encode()

    parser = JsonRosfinmonitoringParser()
    entries = parser.parse(json_content)

    assert len(entries) == 1
    assert entries[0].full_name == "Иванов Иван Иванович"


def test_xml_parser_basic() -> None:
    """Test XML parser with basic data."""
    xml_content = """<?xml version="1.0" encoding="UTF-8"?>
    <root>
        <person>
            <full_name>Иванов Иван Иванович</full_name>
            <birth_date>01.01.1980</birth_date>
            <inclusion_reason>Тест</inclusion_reason>
        </person>
        <person>
            <full_name>Петров Петр Петрович</full_name>
            <birth_date>15.05.1975</birth_date>
        </person>
    </root>""".encode()

    parser = XmlRosfinmonitoringParser()
    entries = parser.parse(xml_content)

    assert len(entries) == 2
    assert entries[0].full_name == "Иванов Иван Иванович"
    assert entries[0].birth_date is not None
    assert entries[0].birth_date.year == 1980
    assert entries[1].full_name == "Петров Петр Петрович"


def test_parser_handles_empty_content() -> None:
    """Test parser handles empty content gracefully."""
    csv_content = b"full_name,birth_date\n"

    parser = CsvRosfinmonitoringParser()
    entries = parser.parse(csv_content)

    assert len(entries) == 0


def test_parser_handles_missing_fields() -> None:
    """Test parser handles missing optional fields."""
    csv_content = "full_name,birth_date\nИванов Иван Иванович,".encode()

    parser = CsvRosfinmonitoringParser()
    entries = parser.parse(csv_content)

    assert len(entries) == 1
    assert entries[0].full_name == "Иванов Иван Иванович"
    assert entries[0].birth_date is None
