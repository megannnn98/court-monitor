"""Unit tests: SourceStats owns its own accumulation and rendering.

These counters used to be re-implemented per entry point over a plain dict,
and the copies had drifted — which is what these tests exist to prevent.
"""

from __future__ import annotations

from court_monitor.services import SourceStats


def test_accumulate_sums_every_field():
    totals = SourceStats()
    totals.accumulate(SourceStats(fetched=5, new_documents=3, duplicates=2, parsed=2, irrelevant=1))
    totals.accumulate(SourceStats(fetched=4, new_documents=1, duplicates=3, failed=1, blocked=1))

    assert totals.fetched == 9
    assert totals.new_documents == 4
    assert totals.duplicates == 5
    assert totals.parsed == 2
    assert totals.irrelevant == 1
    assert totals.failed == 1
    assert totals.blocked == 1


def test_accumulate_carries_blocked():
    """The background job's private copy dropped this field, so a blocked
    source was invisible to an operator who started the run from the web —
    exactly what D-011 exists to surface."""
    totals = SourceStats()
    totals.accumulate(SourceStats(blocked=3))
    assert totals.blocked == 3
    assert totals.as_dict()["blocked"] == 3


def test_as_dict_exposes_every_counter():
    stats = SourceStats(
        fetched=1, new_documents=2, duplicates=3, parsed=4, irrelevant=5, failed=6, blocked=7
    )
    assert stats.as_dict() == {
        "fetched": 1,
        "new": 2,
        "duplicates": 3,
        "parsed": 4,
        "irrelevant": 5,
        "failed": 6,
        "blocked": 7,
    }


def test_summary_mentions_every_counter():
    line = SourceStats(fetched=2, new_documents=1, duplicates=1, parsed=1).summary()
    for key in ("fetched", "new", "duplicates", "parsed", "irrelevant", "failed", "blocked"):
        assert key in line


def test_needs_attention_only_on_failure_or_block():
    assert not SourceStats(fetched=2, parsed=2).needs_attention
    assert SourceStats(failed=1).needs_attention
    assert SourceStats(blocked=1).needs_attention


def test_accumulate_does_not_touch_the_source():
    one = SourceStats(fetched=1)
    two = SourceStats(fetched=2)
    one.accumulate(two)
    assert two.fetched == 2
