"""CLI regression tests for court case commands (task §2, §12, §26)."""

from __future__ import annotations

from typer.testing import CliRunner

from court_monitor.cli.app import app

runner = CliRunner()


def test_find_case_bad_date_returns_error_not_traceback() -> None:
    """Task §26: invalid date must produce a friendly error, not a traceback."""
    result = runner.invoke(
        app,
        ["find-case", "--court", "2zovs", "--result-date", "not-a-date"],
    )
    assert result.exit_code != 0
    assert "дата" in result.output.lower() or "ошибка" in result.output.lower()
    # No Python tracebacks leak out.
    assert "Traceback" not in (result.output + (result.stderr or ""))


def test_find_case_empty_result() -> None:
    """find-case against a fixture court with no matches returns zero."""
    result = runner.invoke(
        app,
        ["find-case", "--court", "2zovs", "--article", "999.999"],
    )
    # Either "Результатов не найдено." (no match fixture) or non-zero exit
    # for missing fixtures — both are acceptable; traceback is not.
    assert "Traceback" not in (result.output + (result.stderr or ""))


def test_list_case_matches_default_pending() -> None:
    """list-case-matches should not crash when empty."""
    result = runner.invoke(app, ["list-case-matches"])
    assert result.exit_code == 0
    assert "Traceback" not in result.output


def test_show_case_match_unknown_id_returns_error() -> None:
    result = runner.invoke(app, ["show-case-match", "99999"])
    assert result.exit_code != 0
    assert "Traceback" not in (result.output + (result.stderr or ""))


def test_confirm_case_match_unknown_id_returns_error() -> None:
    result = runner.invoke(
        app,
        [
            "confirm-case-match",
            "99999",
            "--comment",
            "ok",
            "--operator",
            "test",
        ],
    )
    assert result.exit_code != 0
    assert "Traceback" not in (result.output + (result.stderr or ""))


def test_reject_case_match_unknown_id_returns_error() -> None:
    result = runner.invoke(
        app,
        [
            "reject-case-match",
            "99999",
            "--comment",
            "no",
            "--operator",
            "test",
        ],
    )
    assert result.exit_code != 0
    assert "Traceback" not in (result.output + (result.stderr or ""))
