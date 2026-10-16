"""Table, charts and a recommendation from a download benchmark's JSON rows.

    PYTHONPATH=src uv run python -m ingestion.benchmark_report reports/download_benchmark.json

Writes `<name>_report.md`, `<name>_throughput.svg` and `<name>_latency.svg` next to
the input.  It reads the measurements only; it never downloads anything.
"""

from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from statistics import median
from typing import Any, Literal
from xml.sax.saxutils import escape

# A level within this share of the best throughput is as good as the best one.
SUFFICIENT_SHARE = 0.95
# Categorical slots 1–4 of the dataviz reference palette, in fixed order (light mode).
SERIES_COLORS = ("#2a78d6", "#eb6834", "#1baf7a", "#eda100")


@dataclass(frozen=True)
class LevelSummary:
    """Every repetition of one (global, per-domain) pair."""

    global_limit: int
    per_domain: int
    runs: int
    throughput_median: float
    throughput_min: float
    throughput_max: float
    p95_ms_median: float
    failed: int
    throttled: int
    refused: int
    retries: int
    # Requests that can be in flight at once whatever the global limit: domains × per-domain.
    ceiling: int

    @property
    def safe(self) -> bool:
        return not (self.failed or self.throttled or self.refused)


def summarize(rows: Sequence[Mapping[str, Any]]) -> list[LevelSummary]:
    groups: dict[tuple[int, int], list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[(row["per_domain_concurrency"], row["global_concurrency"])].append(row)
    summaries = []
    for (per_domain, global_limit), runs in sorted(groups.items()):
        throughputs = [float(run["throughput_articles_per_second"]) for run in runs]
        fetches = [run["fetch"] for run in runs]
        summaries.append(
            LevelSummary(
                global_limit=global_limit,
                per_domain=per_domain,
                runs=len(runs),
                throughput_median=median(throughputs),
                throughput_min=min(throughputs),
                throughput_max=max(throughputs),
                p95_ms_median=median(float(fetch["latency_ms"]["p95"]) for fetch in fetches),
                failed=sum(int(run["failed"]) for run in runs),
                throttled=sum(int(fetch["throttled"]) for fetch in fetches),
                refused=sum(int(fetch["refused"]) for fetch in fetches),
                retries=sum(int(fetch["retries"]) for fetch in fetches),
                ceiling=max(len(fetch["per_domain"]) for fetch in fetches) * per_domain,
            )
        )
    return summaries


def recommend(summaries: Sequence[LevelSummary]) -> dict[int, LevelSummary | None]:
    """Per per-domain limit: the smallest safe global limit close enough to the best.

    A level where a site answered 429/403 or an article was lost is not a candidate,
    however fast it was.  Above the domain ceiling the global limit no longer binds,
    so only the first level that reaches the ceiling stands for all the higher ones:
    their differences are noise."""
    choices: dict[int, LevelSummary | None] = {}
    for per_domain in sorted({summary.per_domain for summary in summaries}):
        level = sorted(
            (s for s in summaries if s.per_domain == per_domain), key=lambda s: s.global_limit
        )
        binding = [s for s in level if s.global_limit < s.ceiling]
        binding += [s for s in level if s.global_limit >= s.ceiling][:1]
        safe = [s for s in binding if s.safe]
        if not safe:
            choices[per_domain] = None
            continue
        best = max(s.throughput_median for s in safe)
        choices[per_domain] = min(
            (s for s in safe if s.throughput_median >= best * SUFFICIENT_SHARE),
            key=lambda s: s.global_limit,
        )
    return choices


def _nice_step(raw: float) -> float:
    """The smallest of 1, 2, 2.5, 5 × 10^n not below `raw`, for round tick labels."""
    if raw <= 0:
        return 1.0
    magnitude = 10 ** math.floor(math.log10(raw))
    return next(m * magnitude for m in (1, 2, 2.5, 5, 10) if m * magnitude >= raw)


def render_svg(
    summaries: Sequence[LevelSummary],
    *,
    value: Literal["throughput", "latency"],
    title: str,
    unit: str,
) -> str:
    """A line per per-domain limit over a log2 global axis; whiskers are min–max."""
    width, height = 640, 360
    left, right, top, bottom = 64, 96, 48, 44
    levels = sorted({s.global_limit for s in summaries})
    series = sorted({s.per_domain for s in summaries})

    def y_value(s: LevelSummary) -> float:
        return s.throughput_median if value == "throughput" else s.p95_ms_median

    top_value = max(
        (s.throughput_max if value == "throughput" else s.p95_ms_median) for s in summaries
    )
    y_step = _nice_step(top_value * 1.05 / 4)
    y_max = y_step * 4
    x_span = math.log2(levels[-1]) or 1.0
    legend_x = width - 16 - 100 * len(series)

    def x(level: int) -> float:
        return left + (width - left - right) * math.log2(level) / x_span

    def y(amount: float) -> float:
        return top + (height - top - bottom) * (1 - amount / y_max)

    parts = [
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
            f'width="{width}" height="{height}" font-family="system-ui, sans-serif" font-size="12">'
        ),
        f'<rect width="{width}" height="{height}" fill="#fcfcfb"/>',
        f'<text x="{left}" y="20" font-size="14" fill="#0b0b0b">{escape(title)}</text>',
    ]
    for step in range(5):
        amount = y_step * step
        parts.append(
            f'<line x1="{left}" x2="{width - right}" y1="{y(amount):.1f}" y2="{y(amount):.1f}" '
            'stroke="#e4e3df" stroke-width="1"/>'
            f'<text x="{left - 6}" y="{y(amount) + 4:.1f}" text-anchor="end" '
            f'fill="#52514e">{amount:g}</text>'
        )
    for level in levels:
        parts.append(
            f'<text x="{x(level):.1f}" y="{height - bottom + 16}" text-anchor="middle" '
            f'fill="#52514e">{level}</text>'
        )
    parts.append(
        f'<text x="{(left + width - right) / 2}" y="{height - 8}" text-anchor="middle" '
        'fill="#52514e">global concurrency (log scale)</text>'
        f'<text x="14" y="{top - 14}" fill="#52514e">{escape(unit)}</text>'
    )
    for index, per_domain in enumerate(series):
        color = SERIES_COLORS[index % len(SERIES_COLORS)]
        points = [s for s in summaries if s.per_domain == per_domain]
        coords = " ".join(f"{x(s.global_limit):.1f},{y(y_value(s)):.1f}" for s in points)
        parts.append(f'<polyline points="{coords}" fill="none" stroke="{color}" stroke-width="2"/>')
        for s in points:
            if value == "throughput":
                parts.append(
                    f'<line x1="{x(s.global_limit):.1f}" x2="{x(s.global_limit):.1f}" '
                    f'y1="{y(s.throughput_min):.1f}" y2="{y(s.throughput_max):.1f}" '
                    f'stroke="{color}" stroke-width="1" opacity="0.6"/>'
                )
            parts.append(
                f'<circle cx="{x(s.global_limit):.1f}" cy="{y(y_value(s)):.1f}" r="4" '
                f'fill="{color}" stroke="#fcfcfb" stroke-width="2">'
                f"<title>per-domain {per_domain}, global {s.global_limit}: "
                f"{y_value(s):.3g} {escape(unit)} (runs {s.runs})</title></circle>"
            )
        last = points[-1]
        parts.append(
            f'<text x="{x(last.global_limit) + 8:.1f}" y="{y(y_value(last)) + 4:.1f}" '
            f'fill="#0b0b0b">per-domain {per_domain}</text>'
        )
        parts.append(
            f'<circle cx="{legend_x + index * 100}" cy="16" r="4" fill="{color}"/>'
            f'<text x="{legend_x + 8 + index * 100}" y="20" fill="#52514e">'
            f"per-domain {per_domain}</text>"
        )
    parts.append("</svg>")
    return "\n".join(parts)


def render_markdown(
    rows: Sequence[Mapping[str, Any]],
    summaries: Sequence[LevelSummary],
    choices: Mapping[int, LevelSummary | None],
    *,
    charts: Sequence[str],
) -> str:
    sources = sorted({str(row["sources"]) for row in rows})
    fewest_runs = min(s.runs for s in summaries)
    lines = [
        "# Download benchmark",
        "",
        (
            f"Sources: {', '.join(sources)}. Runs: {len(rows)}; "
            f"repetitions per level: {fewest_runs}–{max(s.runs for s in summaries)}."
        ),
        "",
    ]
    if fewest_runs < 3:
        lines += [
            (
                f"**Warning:** some levels have only {fewest_runs} run(s); "
                "medians below are not stable."
            ),
            "",
        ]
    lines += [
        (
            "| per-domain | global | runs | articles/s median (min–max) | p95 ms | failed "
            "| 429 | 403 | retries | note |"
        ),
        "|---:|---:|---:|---|---:|---:|---:|---:|---:|---|",
    ]
    for s in summaries:
        note = f"above ceiling {s.ceiling}: domain gates bind" if s.global_limit > s.ceiling else ""
        lines.append(
            f"| {s.per_domain} | {s.global_limit} | {s.runs} | {s.throughput_median:.2f} "
            f"({s.throughput_min:.2f}–{s.throughput_max:.2f}) | {s.p95_ms_median:.0f} "
            f"| {s.failed} | {s.throttled} | {s.refused} | {s.retries} | {note} |"
        )
    lines += ["", "## Recommendation", ""]
    for per_domain, choice in choices.items():
        if choice is None:
            lines.append(f"- per-domain {per_domain}: no level without 429/403 or lost articles.")
            continue
        lines.append(
            f"- per-domain {per_domain}: global {choice.global_limit} — "
            f"{choice.throughput_median:.2f} articles/s, the smallest safe level within "
            f"{SUFFICIENT_SHARE:.0%} of the best median."
        )
        if choice.global_limit >= choice.ceiling:
            lines.append(
                f"  Ceiling {choice.ceiling} reached: more domains are needed to measure "
                "higher global limits."
            )
    lines += [""] + [f"![{chart}]({chart})" for chart in charts] + [""]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    args = parser.parse_args()
    rows = json.loads(args.input.read_text(encoding="utf-8"))
    if not rows:
        raise SystemExit(f"{args.input}: no runs")
    summaries = summarize(rows)
    stem = args.input.with_suffix("")
    charts = {
        f"{stem.name}_throughput.svg": render_svg(
            summaries, value="throughput", title="Download throughput", unit="articles/s"
        ),
        f"{stem.name}_latency.svg": render_svg(
            summaries, value="latency", title="Request latency p95 (median of runs)", unit="ms"
        ),
    }
    for name, svg in charts.items():
        (args.input.parent / name).write_text(svg + "\n", encoding="utf-8")
    report = args.input.parent / f"{stem.name}_report.md"
    report.write_text(
        render_markdown(rows, summaries, recommend(summaries), charts=list(charts)),
        encoding="utf-8",
    )
    print(report)


if __name__ == "__main__":
    main()
