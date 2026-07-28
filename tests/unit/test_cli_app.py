"""Unit tests: small CLI helpers in cli/app.py.

The `run-all` command itself is thin orchestration over process_source /
process_registry_source / generate_matches (all tested elsewhere) plus
global config/engine lookups — same shape and same lack of direct CLI tests
as the pre-existing `fetch-all` command. Only the pure helper is unit-tested
here.
"""

from __future__ import annotations

import typer

from court_monitor.cli.app import (
    _accumulate_fetch_stats,
    _render_stats_line,
    _render_totals_line,
    _stats_color,
)
from court_monitor.services import SourceStats


def test_accumulate_fetch_stats_sums_fields():
    totals = {"fetched": 0, "new": 0, "dup": 0, "parsed": 0, "irr": 0, "fail": 0, "blocked": 0}
    stats_a = SourceStats(
        fetched=5, new_documents=3, duplicates=2, parsed=2, irrelevant=1, failed=0, blocked=0
    )
    stats_b = SourceStats(
        fetched=4, new_documents=1, duplicates=3, parsed=0, irrelevant=0, failed=1, blocked=1
    )

    _accumulate_fetch_stats(totals, stats_a)
    _accumulate_fetch_stats(totals, stats_b)

    assert totals == {
        "fetched": 9,
        "new": 4,
        "dup": 5,
        "parsed": 2,
        "irr": 1,
        "fail": 1,
        "blocked": 1,
    }


def test_accumulate_fetch_stats_does_not_touch_other_keys():
    totals = {"fetched": 0, "new": 0, "dup": 0, "parsed": 0, "irr": 0, "fail": 0, "blocked": 0}
    stats = SourceStats()  # all zero
    _accumulate_fetch_stats(totals, stats)
    assert all(v == 0 for v in totals.values())


def test_stats_color_clean_is_green():
    assert _stats_color(SourceStats(fetched=2, parsed=2)) == typer.colors.GREEN


def test_stats_color_failed_is_yellow():
    assert _stats_color(SourceStats(failed=1)) == typer.colors.YELLOW


def test_stats_color_blocked_is_yellow():
    assert _stats_color(SourceStats(blocked=1)) == typer.colors.YELLOW


def test_stats_color_skipped_is_yellow_even_if_clean():
    assert _stats_color(SourceStats(), skipped=True) == typer.colors.YELLOW


def test_render_stats_line_includes_all_fields():
    stats = SourceStats(
        fetched=2, new_documents=1, duplicates=1, parsed=1, irrelevant=0, failed=0, blocked=0
    )
    line = _render_stats_line(stats)
    assert line == ("fetched=2 new=1 duplicates=1 parsed=1 irrelevant=0 failed=0 blocked=0")


def test_render_totals_line_includes_all_fields():
    totals = {"fetched": 9, "new": 4, "dup": 5, "parsed": 2, "irr": 1, "fail": 1, "blocked": 1}
    line = _render_totals_line(totals)
    assert line == ("fetched=9 new=4 duplicates=5 parsed=2 irrelevant=1 failed=1 blocked=1")
