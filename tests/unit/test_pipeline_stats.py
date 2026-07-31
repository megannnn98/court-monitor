"""The tally shared by the stats types, and the typed match result.

Both replace shapes that let a mistake stay silent: a three-branch cascade
copy-pasted across two classes, and a plain dict whose keys existed only in a
docstring — one of which the CLI read and never got.
"""

from __future__ import annotations

import dataclasses

import pytest

from court_monitor.domain.models import ParserStatus
from court_monitor.matching.candidates import MatchStats
from court_monitor.services import ReprocessAllStats, SourceStats


@pytest.fixture(params=[SourceStats, ReprocessAllStats])
def stats(request):
    """Both stats types keep the same three counters and must tally alike."""
    return request.param()


@pytest.mark.parametrize(
    ("status", "counter"),
    [
        (ParserStatus.parsed.value, "parsed"),
        (ParserStatus.irrelevant.value, "irrelevant"),
        (ParserStatus.parser_failed.value, "failed"),
    ],
)
def test_each_status_lands_in_its_own_counter(stats, status, counter):
    stats.record_parse_outcome(status)

    assert getattr(stats, counter) == 1
    assert stats.parsed + stats.irrelevant + stats.failed == 1


def test_a_status_with_no_counter_is_ignored_rather_than_miscounted(stats):
    """pending/skipped_duplicate are real states with no tally of their own."""
    stats.record_parse_outcome(ParserStatus.pending.value)

    assert (stats.parsed, stats.irrelevant, stats.failed) == (0, 0, 0)


def test_the_shared_base_does_not_reorder_the_dataclass_fields():
    """The mixin is deliberately not a dataclass. If it became one, its fields
    would jump to the front of every subclass constructor."""
    assert [f.name for f in dataclasses.fields(SourceStats)][:3] == [
        "fetched",
        "new_documents",
        "duplicates",
    ]
    assert dataclasses.fields(ReprocessAllStats)[0].name == "documents"


# ---------------------------------------------------------------------------
# MatchStats
# ---------------------------------------------------------------------------


def test_match_stats_reports_every_key_the_job_result_publishes():
    """as_dict feeds the job result an operator reads; a key silently vanishing
    from it is the failure the dict-based version already shipped."""
    assert set(MatchStats().as_dict()) == {
        "facts_person",
        "candidates_created",
        "already_existed",
        "no_candidates",
        "errors",
    }


def test_a_field_that_does_not_exist_is_an_error_not_a_dash():
    """The CLI used to ask a plain dict for 'documents_processed' — a key
    generate_matches never set — and printed '-' on every run instead."""
    with pytest.raises(AttributeError):
        _ = MatchStats().documents_processed  # type: ignore[attr-defined]
