"""Age is evidence about the case, never the news publication or a person's first sighting."""

from datetime import date

import pytest

from channel_feed.case_age import CaseAge, case_timing


@pytest.mark.parametrize(
    ("quotation", "expected"),
    [
        ("Сегодня против жителя возбудили уголовное дело по ст. 275 УК РФ.", CaseAge.NEW),
        ("22 сентября 2026 года возбуждено уголовное дело.", CaseAge.NEW),
        ("22.09.2026 против мужчины завели уголовное дело.", CaseAge.NEW),
        ("В августе 2026 года против мужчины возбудили уголовное дело.", CaseAge.NEW),
        ("В январе 2026 года против мужчины возбудили уголовное дело.", CaseAge.UPDATE),
        ("Мужчину задержали в 2023 году по делу о госизмене.", CaseAge.UPDATE),
        ("Суд вынес приговор по делу, возбужденному в 2023 году.", CaseAge.UPDATE),
        ("Вчера суд вынес приговор по ст. 275 УК РФ.", CaseAge.UNKNOWN),
        ("Возбудили уголовное дело по ст. 275 УК РФ.", CaseAge.UNKNOWN),
        ("Сегодня сообщили, что ранее против него возбудили уголовное дело.", CaseAge.UNKNOWN),
        ("Сегодня следователь может возбудить уголовное дело.", CaseAge.UNKNOWN),
        ("Сегодня уголовное дело не возбуждено.", CaseAge.UNKNOWN),
        ("Сегодня потребовали, чтобы против него возбудили уголовное дело.", CaseAge.UNKNOWN),
        ("Сегодня арестовали мужчину 1990 года рождения.", CaseAge.UNKNOWN),
        ("В сентябре 2026 года против мужчины возбудили уголовное дело.", CaseAge.UNKNOWN),
        ("31 сентября 2026 года возбуждено дело.", CaseAge.UNKNOWN),
        ("В 2023 году возбудили дело, сегодня суд вынес приговор.", CaseAge.UNKNOWN),
        ("В 2026 году возбуждено уголовное дело.", CaseAge.UNKNOWN),
        ("1 октября 2026 года возбуждено уголовное дело.", CaseAge.UNKNOWN),
        # A negation about something else does not question when the case was opened.
        (
            "22 сентября 2026 года возбуждено уголовное дело, обвиняемый не признал вину.",
            CaseAge.NEW,
        ),
        ("22 сентября 2026 года уголовное дело так и не было возбуждено.", CaseAge.UNKNOWN),
    ],
)
def test_case_age_needs_explicit_unambiguous_time(quotation: str, expected: CaseAge) -> None:
    result = case_timing(
        quotation,
        published=date(2026, 9, 23),
        period_start=date(2026, 8, 1),
        period_end=date(2026, 9, 23),
    )
    assert result.age is expected
    assert result.reason


def test_relative_day_crossing_period_boundary_is_not_new() -> None:
    result = case_timing(
        "Вчера возбудили уголовное дело.",
        published=date(2026, 8, 1),
        period_start=date(2026, 8, 1),
        period_end=date(2026, 9, 23),
    )
    assert result.age is CaseAge.UPDATE


def test_month_partially_overlapping_period_stays_unknown() -> None:
    result = case_timing(
        "В августе 2026 года возбудили уголовное дело.",
        published=date(2026, 9, 23),
        period_start=date(2026, 8, 15),
        period_end=date(2026, 9, 23),
    )
    assert result.age is CaseAge.UNKNOWN
