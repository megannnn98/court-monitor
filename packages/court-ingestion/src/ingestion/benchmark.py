"""Small, polite, download-only benchmark for registered public sources.

Run it outside a production scheduler.  It discovers a capped sample once, then
downloads only that sample; it never parses, stores, extracts, or calls an LLM.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import resource
import sys
from collections import Counter
from collections.abc import Mapping
from dataclasses import asdict
from pathlib import Path
from time import monotonic

import httpx

from ingestion.concurrency import FetchLimits, FetchMetrics, build_http_client
from ingestion.sources.source_registry import SOURCES
from ingestion.sources.website_adapter import WebsiteAdapter, bind_http_client
from monitor_core.ingestion import RetryingDocumentFetcher
from monitor_core.model import SourceReference

DEFAULT_LEVELS = (1, 2, 4, 8, 12, 16, 24, 32, 48, 64)


def _save_rows(rows: list[dict[str, object]], output_path: Path) -> None:
    """Checkpoint after each run so Ctrl-C or a site limit loses no measurements."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with output_path.with_suffix(".csv").open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=sorted(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


async def _sample(source: str, limit: int) -> list[SourceReference]:
    definition = SOURCES[source]
    async with build_http_client(
        FetchLimits(max_concurrency=1, per_domain_concurrency=1)
    ) as client:
        adapter = definition.create_adapter(client, WebsiteAdapter())
        return list(await adapter.discover(limit=limit))


async def _run(
    samples: Mapping[str, list[SourceReference]], limits: FetchLimits
) -> dict[str, object]:
    """Download every selected source through one pooled client and worker pool."""
    metrics = FetchMetrics()
    async with build_http_client(limits, metrics=metrics) as client:
        adapters = {}
        for source in samples:
            fetcher = RetryingDocumentFetcher(
                WebsiteAdapter(),
                max_attempts=limits.max_attempts,
                base_delay_seconds=limits.base_delay_seconds,
                max_delay_seconds=limits.max_delay_seconds,
                jitter_ratio=limits.jitter_ratio,
                on_retry=lambda reference: metrics.retried(httpx.URL(reference.url).host),
            )
            bind_http_client(fetcher, client)
            adapters[source] = SOURCES[source].create_adapter(client, fetcher)
        queue: asyncio.Queue[tuple[str, SourceReference] | None] = asyncio.Queue(
            maxsize=limits.max_concurrency
        )
        succeeded: Counter[str] = Counter()
        failed: Counter[str] = Counter()

        async def worker() -> None:
            while True:
                item = await queue.get()
                try:
                    if item is None:
                        return
                    source, reference = item
                    try:
                        await adapters[source].fetch(reference)
                        succeeded[source] += 1
                    except Exception:  # noqa: BLE001 - benchmark records aggregate failures
                        failed[source] += 1
                finally:
                    queue.task_done()

        article_count = sum(map(len, samples.values()))
        workers = [
            asyncio.create_task(worker()) for _ in range(min(article_count, limits.max_concurrency))
        ]
        started = monotonic()
        for source, references in samples.items():
            for reference in references:
                await queue.put((source, reference))
        await queue.join()
        for _ in workers:
            await queue.put(None)
        await asyncio.gather(*workers)
        elapsed = monotonic() - started
    report = metrics.report()
    return {
        "sources": ",".join(sorted(samples)),
        "global_concurrency": limits.max_concurrency,
        "per_domain_concurrency": limits.per_domain_concurrency,
        "articles": article_count,
        "succeeded": sum(succeeded.values()),
        "failed": sum(failed.values()),
        "by_source": {
            source: {
                "articles": len(references),
                "succeeded": succeeded[source],
                "failed": failed[source],
            }
            for source, references in samples.items()
        },
        "throughput_articles_per_second": round(sum(succeeded.values()) / elapsed, 3)
        if elapsed
        else 0.0,
        "rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "fetch": asdict(report),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources", nargs="+", choices=sorted(SOURCES), required=True)
    parser.add_argument("--sample-size", type=int, default=10)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--per-domain", type=int, nargs="+", default=(1, 2))
    parser.add_argument(
        "--levels",
        type=int,
        nargs="+",
        default=DEFAULT_LEVELS,
        help="global limits to measure; above domains × per-domain they no longer bind",
    )
    parser.add_argument(
        "--continue-on-restriction",
        action="store_true",
        help="continue after an HTTP 429 or 403 (unsafe; disabled by default)",
    )
    parser.add_argument("--output", type=Path, default=Path("reports/download_benchmark.json"))
    args = parser.parse_args()
    if (
        args.sample_size < 1
        or args.repetitions < 3
        or min(args.per_domain) < 1
        or min(args.levels) < 1
    ):
        raise SystemExit(
            "sample-size, per-domain and levels must be positive; repetitions must be at least 3"
        )
    levels = tuple(sorted(set(args.levels)))

    samples: dict[str, list[SourceReference]] = {}
    for source in args.sources:
        print(
            f"[benchmark] discovering source={source} limit={args.sample_size}",
            file=sys.stderr,
            flush=True,
        )
        samples[source] = asyncio.run(_sample(source, args.sample_size))
        print(
            f"[benchmark] discovered source={source} articles={len(samples[source])}",
            file=sys.stderr,
            flush=True,
        )
    rows: list[dict[str, object]] = []
    total = args.repetitions * len(levels) * len(args.per_domain)
    run_number = 0
    # Interleaved levels avoid making a warmed connection or a transient outage favour one level.
    for repetition in range(args.repetitions):
        for global_limit in levels[repetition % 2 :] + levels[: repetition % 2]:
            for per_domain in args.per_domain:
                run_number += 1
                print(
                    f"[benchmark] run={run_number}/{total} repetition={repetition + 1}/"
                    f"{args.repetitions} sources={len(samples)} global={global_limit} "
                    f"per_domain={per_domain}",
                    file=sys.stderr,
                    flush=True,
                )
                row = asyncio.run(
                    _run(
                        samples,
                        FetchLimits(
                            max_concurrency=global_limit,
                            per_domain_concurrency=per_domain,
                        ),
                    )
                )
                rows.append(row)
                _save_rows(rows, args.output)
                print(
                    f"[benchmark] complete run={run_number}/{total} "
                    f"success={row['succeeded']}/{row['articles']} "
                    f"throughput={row['throughput_articles_per_second']} articles/s",
                    file=sys.stderr,
                    flush=True,
                )
                fetch = row["fetch"]
                assert isinstance(fetch, dict)
                if (
                    fetch.get("throttled", 0) or fetch.get("refused", 0)
                ) and not args.continue_on_restriction:
                    print(
                        "[benchmark] stopping: source returned HTTP 429/403; "
                        "results were checkpointed",
                        file=sys.stderr,
                        flush=True,
                    )
                    return
    print(f"[benchmark] saved results={args.output}", file=sys.stderr, flush=True)


if __name__ == "__main__":
    main()
