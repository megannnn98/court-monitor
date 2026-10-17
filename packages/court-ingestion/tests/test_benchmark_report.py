from __future__ import annotations

from ingestion.benchmark_report import recommend, render_svg, summarize


def _row(
    global_limit: int,
    per_domain: int,
    throughput: float,
    *,
    throttled: int = 0,
    failed: int = 0,
    p95: float = 500.0,
) -> dict[str, object]:
    return {
        "global_concurrency": global_limit,
        "per_domain_concurrency": per_domain,
        "articles": 9,
        "succeeded": 9 - failed,
        "failed": failed,
        "throughput_articles_per_second": throughput,
        "fetch": {
            "throttled": throttled,
            "refused": 0,
            "retries": 0,
            "latency_ms": {"p95": p95},
            "per_domain": {"a.example": {}, "b.example": {}, "c.example": {}},
        },
    }


def test_repetitions_of_one_level_become_one_median_with_its_range() -> None:
    rows = [_row(4, 1, 3.0), _row(4, 1, 1.0), _row(4, 1, 2.0, p95=900.0)]

    [level] = summarize(rows)

    assert (level.global_limit, level.per_domain, level.runs) == (4, 1, 3)
    assert level.throughput_median == 2.0
    assert (level.throughput_min, level.throughput_max) == (1.0, 3.0)
    assert level.p95_ms_median == 500.0
    assert level.ceiling == 3


def test_recommends_the_smallest_level_within_five_percent_of_the_best() -> None:
    rows = [_row(1, 1, 1.0), _row(2, 1, 2.0), _row(4, 1, 3.9), _row(8, 1, 4.0)]

    choice = recommend(summarize(rows))[1]

    assert choice is not None
    assert choice.global_limit == 4


def test_a_level_a_site_throttled_or_that_lost_articles_is_never_recommended() -> None:
    rows = [
        _row(1, 1, 1.0),
        _row(4, 1, 2.0),
        _row(8, 1, 9.0, throttled=1),
        _row(16, 1, 9.0, failed=1),
    ]

    choice = recommend(summarize(rows))[1]

    assert choice is not None
    assert choice.global_limit == 4


def test_no_safe_level_means_no_recommendation() -> None:
    assert recommend(summarize([_row(4, 1, 2.0, throttled=1)])) == {1: None}


def test_chart_has_one_series_per_per_domain_limit_and_a_hover_title_per_point() -> None:
    svg = render_svg(
        summarize([_row(1, 1, 1.0), _row(2, 1, 2.0), _row(1, 2, 1.5)]),
        value="throughput",
        title="Throughput",
        unit="articles/s",
    )

    assert svg.startswith("<svg")
    assert svg.count("<polyline") == 2
    assert svg.count("<title>") == 3


def test_levels_above_the_domain_ceiling_are_noise_and_never_recommended() -> None:
    # Three domains × per-domain 1: global 4 already allows every possible request.
    rows = [_row(2, 1, 2.0), _row(4, 1, 3.0), _row(8, 1, 3.9), _row(64, 1, 4.2)]

    choice = recommend(summarize(rows))[1]

    assert choice is not None
    assert choice.global_limit == 4
