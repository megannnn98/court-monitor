"""Tests for sud_delo case card parser."""

from __future__ import annotations

from pathlib import Path

from court_monitor.parsers.sud_delo import parse_case_card

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "sudrf-live" / "2zovs" / "sud_delo"


def test_parse_case_card_example() -> None:
    """Test parsing a real case card from 2ZOV."""
    fixture_file = FIXTURE_DIR / "case-card-example.html"
    html = fixture_file.read_text(encoding="utf-8")

    result = parse_case_card(html, case_uid="ec9975ee-3df8-4c00-8ab0-c6620d4ffd1b")

    # Basic info
    assert result.case_number == "22К-1372/2026"
    assert result.case_uid == "ec9975ee-3df8-4c00-8ab0-c6620d4ffd1b"
    assert result.received_at is not None
    assert result.received_at.year == 2026
    assert result.received_at.month == 8
    assert result.received_at.day == 10
    assert result.judge == "Белоусов Олег Александрович"

    # First instance
    assert result.first_instance_court == "Ярославский ГВС"
    assert result.first_instance_case_number == "3/1-12/2026 (К/21548)"
    assert result.first_instance_judge == "ПЛОТНИКОВ Геннадий Александрович"

    # Persons
    assert len(result.persons) == 1
    person = result.persons[0]
    assert person.name == "Иванов Никита Сергеевич"
    assert len(person.articles) == 1
    assert "ст.111 ч.1 УК РФ" in person.articles[0]
    assert "заключения под стражу" in person.material

    # Events
    assert len(result.events) == 2
    assert result.events[0].event_type == "Передача дела судье"
    assert result.events[0].event_date is not None
    assert result.events[0].event_date.day == 10
    assert result.events[0].event_time == "12:14"

    assert result.events[1].event_type == "Судебное заседание"
    assert result.events[1].event_date is not None
    assert result.events[1].event_date.day == 12
    assert result.events[1].event_time == "09:00"

    # Publication info
    assert result.published_at is not None
    assert result.published_at.year == 2026
    assert result.published_at.month == 8
    assert result.published_at.day == 10
    assert result.published_at.hour == 12
    assert result.published_at.minute == 30

    assert result.modified_at is not None
    assert result.modified_at.day == 11


def test_parse_case_card_minimal() -> None:
    """Test parsing minimal case card HTML."""
    html = """
    <html>
    <body>
    <div class="casenumber">ДЕЛО № 1-123/2026</div>
    <div id="cont1">
        <table>
            <tr>
                <td>Дата поступления</td>
                <td>01.01.2026</td>
            </tr>
            <tr>
                <td>Судья</td>
                <td>Иванов И.И.</td>
            </tr>
        </table>
    </div>
    <div id="cont4">
        <table>
            <tr>
                <td>Фамилия / наименование</td>
                <td>Перечень статей</td>
            </tr>
            <tr>
                <td>Петров П.П.</td>
                <td>ст.205 УК РФ</td>
            </tr>
        </table>
    </div>
    </body>
    </html>
    """

    result = parse_case_card(html)

    assert result.case_number == "1-123/2026"
    assert result.received_at is not None
    assert result.received_at.year == 2026
    assert result.judge == "Иванов И.И."
    assert len(result.persons) == 1
    assert result.persons[0].name == "Петров П.П."
    assert "ст.205 УК РФ" in result.persons[0].articles[0]


def test_parse_articles_multiple() -> None:
    """Test parsing multiple articles."""
    html = """
    <html>
    <body>
    <div id="cont4">
        <table>
            <tr>
                <td>Фамилия / наименование</td>
                <td>Перечень статей</td>
            </tr>
            <tr>
                <td>Сидоров С.С.</td>
                <td>ст.205 УК РФ; ст.205.1 УК РФ</td>
            </tr>
        </table>
    </div>
    </body>
    </html>
    """

    result = parse_case_card(html)

    assert len(result.persons) == 1
    assert len(result.persons[0].articles) == 2
    assert "ст.205 УК РФ" in result.persons[0].articles[0]
    assert "ст.205.1 УК РФ" in result.persons[0].articles[1]


def test_parse_case_card_hidden_person() -> None:
    """Test parsing case card with hidden person info."""
    html = """
    <html>
    <body>
    <div class="casenumber">ДЕЛО № 1-456/2026</div>
    <div id="cont4">
        <table>
            <tr>
                <td>Фамилия / наименование</td>
                <td>Перечень статей</td>
            </tr>
            <tr>
                <td>Информация скрыта</td>
                <td>ст.205.1 ч.1 УК РФ</td>
            </tr>
        </table>
    </div>
    </body>
    </html>
    """

    result = parse_case_card(html)

    assert result.case_number == "1-456/2026"
    assert len(result.persons) == 1
    assert result.persons[0].name == "Информация скрыта"
    assert "ст.205.1 ч.1 УК РФ" in result.persons[0].articles[0]
