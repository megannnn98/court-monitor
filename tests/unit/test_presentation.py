"""Tests for the human-readable CLI presentation layer."""

from __future__ import annotations

import io
import re

import pytest
from rich.console import Console

from court_monitor.presentation.console import ConsoleReporter, _text_safe


@pytest.fixture()
def reporter() -> ConsoleReporter:
    return ConsoleReporter()


@pytest.fixture()
def reporter_verbose() -> ConsoleReporter:
    return ConsoleReporter(verbose=True)


def _capture(reporter: ConsoleReporter) -> str:
    """Capture what the reporter wrote to its console."""
    buf = io.StringIO()
    reporter._console = Console(file=buf, force_terminal=True, no_color=True)
    return buf


def _strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences (for assertion clarity)."""
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def test_reporter_db_path():
    buf = _capture(ConsoleReporter())
    r = ConsoleReporter()
    r._console = Console(file=buf, force_terminal=True, no_color=True)
    r.db_path("/tmp/test.db")
    out = _strip_ansi(buf.getvalue())
    assert "База данных: /tmp/test.db" in out


def test_reporter_live_mode():
    buf = _capture(ConsoleReporter())
    r = ConsoleReporter()
    r._console = Console(file=buf, force_terminal=True, no_color=True)
    r.live_mode(True)
    out = _strip_ansi(buf.getvalue())
    assert "LIVE" in out


def test_reporter_source_started():
    buf = _capture(ConsoleReporter())
    r = ConsoleReporter()
    r._console = Console(file=buf, force_terminal=True, no_color=True)
    r.source_started("2zovs", "2-й Западный окружной военный суд", live=True)
    out = _strip_ansi(buf.getvalue())
    assert "Источник: 2zovs" in out
    assert "Режим: LIVE" in out


def test_reporter_archive_loaded():
    buf = _capture(ConsoleReporter())
    r = ConsoleReporter()
    r._console = Console(file=buf, force_terminal=True, no_color=True)
    r.archive_loaded(months_count=20)
    out = _strip_ansi(buf.getvalue())
    assert "Найдено месяцев: 20" in out


def test_reporter_document_relevant_compact():
    buf = _capture(ConsoleReporter())
    r = ConsoleReporter()
    r._console = Console(file=buf, force_terminal=True, no_color=True)
    r.document_parsed(
        372,
        published="12.08.2026",
        title="Оправдание терроризма",
        articles=["205.2"],
        names_count=2,
        relevant=True,
    )
    out = _strip_ansi(buf.getvalue())
    assert "did=372" in out
    assert "Оправдание терроризма" in out
    assert "205.2" in out
    assert "ФИО: 2" in out


def test_reporter_document_irrelevant_compact():
    buf = _capture(ConsoleReporter())
    r = ConsoleReporter()
    r._console = Console(file=buf, force_terminal=True, no_color=True)
    r.document_parsed(999, relevant=False)
    out = _strip_ansi(buf.getvalue())
    assert "did=999" in out


def test_reporter_document_verbose():
    buf = _capture(ConsoleReporter(verbose=True))
    r = ConsoleReporter(verbose=True)
    r._console = Console(file=buf, force_terminal=True, no_color=True)
    r.document_parsed(
        730,
        published="12.08.2026",
        title="Test doc",
        facts=10,
        articles=["205.2"],
        dates_count=1,
        names_count=2,
        matched_articles=["205.2"],
        matched_keywords=["военный суд", "оправдание терроризма"],
        relevant=True,
    )
    out = _strip_ansi(buf.getvalue())
    assert "Документ #730" in out
    assert "facts: 10" in out
    assert "dates: 1" in out
    assert "names: 2" in out
    assert "военный суд" in out
    assert "оправдание терроризма" in out


def test_reporter_search_results():
    buf = _capture(ConsoleReporter())
    r = ConsoleReporter()
    r._console = Console(file=buf, force_terminal=True, no_color=True)
    r.search_started(article="205.2", date_hint="12.08.2026")
    r.search_results(3)
    out = _strip_ansi(buf.getvalue())
    assert "ст. 205.2" in out
    assert "Найдено результатов: 3" in out


def test_reporter_candidate_found():
    buf = _capture(ConsoleReporter())
    r = ConsoleReporter()
    r._console = Console(file=buf, force_terminal=True, no_color=True)
    r.candidate_found(
        "1-123/2026",
        score=0.85,
        signals=["court", "article", "date"],
        candidate_id=42,
    )
    out = _strip_ansi(buf.getvalue())
    assert "1-123/2026" in out
    assert "score=0.85" in out
    assert "court" in out
    assert "article" in out


def test_reporter_no_match():
    buf = _capture(ConsoleReporter())
    r = ConsoleReporter()
    r._console = Console(file=buf, force_terminal=True, no_color=True)
    r.no_match()
    out = _strip_ansi(buf.getvalue())
    assert "Подходящее дело не найдено" in out


def test_reporter_review_candidate():
    buf = _capture(ConsoleReporter())
    r = ConsoleReporter()
    r._console = Console(file=buf, force_terminal=True, no_color=True)
    r.review_candidate(42, "1-123/2026", 0.85)
    out = _strip_ansi(buf.getvalue())
    assert "Требуется проверка оператора" in out
    assert "кандидат #42" in out


def test_reporter_source_summary():
    buf = _capture(ConsoleReporter())
    r = ConsoleReporter()
    r._console = Console(file=buf, force_terminal=True, no_color=True)
    r.source_started("test", "Test Source")
    r.source_summary(
        fetched=12,
        new_documents=3,
        duplicates=4,
        parsed=2,
        irrelevant=1,
        failed=0,
        blocked=0,
        elapsed=4.2,
    )
    out = _strip_ansi(buf.getvalue())
    assert "Итог" in out
    assert "12" in out
    assert "3" in out
    assert "Готово за" in out


def test_reporter_court_summary():
    buf = _capture(ConsoleReporter())
    r = ConsoleReporter()
    r._console = Console(file=buf, force_terminal=True, no_color=True)
    r.court_summary(
        "2zovs",
        processed=5,
        review_created=1,
        no_match=2,
        temporary_failures=1,
        failed=1,
    )
    out = _strip_ansi(buf.getvalue())
    assert "2zovs" in out


def test_reporter_grand_total():
    buf = _capture(ConsoleReporter())
    r = ConsoleReporter()
    r._console = Console(file=buf, force_terminal=True, no_color=True)
    r.grand_total(
        sources=2,
        new_documents=5,
        review_candidates=2,
        errors=0,
        temporary_errors=1,
    )
    out = _strip_ansi(buf.getvalue())
    assert "ИТОГ" in out
    assert "2" in out
    assert "5" in out


def test_reporter_error():
    buf = _capture(ConsoleReporter())
    r = ConsoleReporter()
    r._console = Console(file=buf, force_terminal=True, no_color=True)
    r.error("HTTP 500 при загрузке архива", exception_type="SudrfTemporaryError")
    out = _strip_ansi(buf.getvalue())
    assert "HTTP 500 при загрузке архива" in out


def test_reporter_empty_group():
    buf = _capture(ConsoleReporter())
    r = ConsoleReporter()
    r._console = Console(file=buf, force_terminal=True, no_color=True)
    r.empty_group("нет источников")
    out = _strip_ansi(buf.getvalue())
    assert "нет источников" in out


def test_no_color_env_respected(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    r = ConsoleReporter()
    assert r._no_color is True
    assert r._console.no_color is True


def test_default_no_color_false(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    r = ConsoleReporter()
    assert not r._no_color


def test_heading_includes_text():
    buf = _capture(ConsoleReporter())
    r = ConsoleReporter()
    r._console = Console(file=buf, force_terminal=True, no_color=True)
    r.heading("Судебные дела")
    out = _strip_ansi(buf.getvalue())
    assert "Судебные дела" in out


def test_match_stats_includes_values():
    buf = _capture(ConsoleReporter())
    r = ConsoleReporter()
    r._console = Console(file=buf, force_terminal=True, no_color=True)
    r.match_stats(created=3, already_existed=1, no_candidates=2, errors=0)
    out = _strip_ansi(buf.getvalue())
    assert "3" in out
    assert "1" in out
    assert "2" in out


def test_reporter_source_summary_includes_blocked():
    buf = _capture(ConsoleReporter())
    r = ConsoleReporter()
    r._console = Console(file=buf, force_terminal=True, no_color=True)
    r.source_started("test", "Test")
    r.source_summary(blocked=2, failed=1, new_documents=5, fetched=8)
    out = _strip_ansi(buf.getvalue())
    assert "заблокировано" in out
    assert "ошибок" in out


@pytest.mark.parametrize(
    "text,expected",
    [
        ("short", "short"),
        ("a" * 100, ("a" * 77) + "..."),
    ],
)
def test_text_safe_truncation(text, expected):
    assert _text_safe(text, 80) == expected
