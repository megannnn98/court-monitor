"""Real-world validation commands (docs/wiki/RealWorldValidation.md).

build-real-world-corpus        discover + fetch (polite) + manifest
real-world-corpus-status       manifest summary and local cache coverage
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

from evaluation.real_world.corpus_builder import (
    DEFAULT_DISCOVERY_LIMIT,
    DEFAULT_EVALUATION_SAMPLE_SIZE,
    DEFAULT_PERIOD_END,
    DEFAULT_PERIOD_START,
    DEFAULT_SAMPLING_SEED,
    DEFAULT_TARGETS,
    DEFAULT_TOTAL_TARGET,
    CorpusBuildConfig,
    RealWorldCorpusBuilder,
    parse_targets,
)
from evaluation.real_world.corpus_cache import RawCorpusCache
from evaluation.real_world.models import (
    DEFAULT_CACHE_DIR,
    DEFAULT_MANIFEST_PATH,
    CorpusManifest,
    load_manifest,
    write_manifest,
)
from evaluation.real_world.replay import CorpusCacheMissError, load_replay_entries
from observability import configure_logging
from sources.source_registry import SOURCES

# Documented exit codes (docs/wiki/RealWorldValidation.md).
EXIT_OK = 0
EXIT_QUALITY_FAILURE = 1
EXIT_INFRASTRUCTURE_ERROR = 2


def add_real_world_arguments(subparsers: Any) -> None:
    build = subparsers.add_parser(
        "build-real-world-corpus",
        help="Discover and fetch the real-world corpus (1 request/s per domain) and write the manifest",
    )
    build.add_argument("--period-start", type=date.fromisoformat, default=DEFAULT_PERIOD_START)
    build.add_argument("--period-end", type=date.fromisoformat, default=DEFAULT_PERIOD_END)
    build.add_argument("--seed", type=int, default=DEFAULT_SAMPLING_SEED)
    build.add_argument(
        "--target",
        action="append",
        default=None,
        help="source=count, repeatable (default ovd-info=600, sota-vision=400)",
    )
    build.add_argument("--total-target", type=int, default=DEFAULT_TOTAL_TARGET)
    build.add_argument("--sample-size", type=int, default=DEFAULT_EVALUATION_SAMPLE_SIZE)
    build.add_argument("--discovery-limit", type=int, default=DEFAULT_DISCOVERY_LIMIT)
    build.add_argument(
        "--min-interval",
        type=float,
        default=1.0,
        help="Seconds between requests to one domain; values below 1.0 are refused",
    )
    build.add_argument("--source", action="append", choices=sorted(SOURCES), default=None)
    build.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    build.add_argument("--manifest-path", type=Path, default=DEFAULT_MANIFEST_PATH)
    mode = build.add_mutually_exclusive_group()
    mode.add_argument(
        "--offline", action="store_true", help="Rebuild the manifest from the cache only"
    )
    mode.add_argument(
        "--from-manifest",
        action="store_true",
        help="Reconstruct: fetch the articles of an existing manifest by URL (no discovery)",
    )

    status = subparsers.add_parser(
        "real-world-corpus-status", help="Summarize the manifest and verify the local cache"
    )
    status.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    status.add_argument("--manifest-path", type=Path, default=DEFAULT_MANIFEST_PATH)


def run_real_world_command(args: argparse.Namespace) -> bool:
    if args.command == "build-real-world-corpus":
        _build(args)
        return True
    if args.command == "real-world-corpus-status":
        _status(args)
        return True
    return False


def _build(args: argparse.Namespace) -> None:
    if args.min_interval < 1.0:
        raise SystemExit("--min-interval below 1.0 second per domain is not allowed")
    configure_logging()
    names = args.source or sorted(SOURCES)
    config = CorpusBuildConfig(
        period_start=args.period_start,
        period_end=args.period_end,
        sampling_seed=args.seed,
        targets=parse_targets(args.target) if args.target else dict(DEFAULT_TARGETS),
        total_target=args.total_target,
        evaluation_sample_size=args.sample_size,
        min_interval_seconds=args.min_interval,
        discovery_limit=args.discovery_limit,
        offline=args.offline,
    )
    builder = RealWorldCorpusBuilder(
        sources={name: SOURCES[name] for name in names},
        cache=RawCorpusCache(args.cache_dir),
        config=config,
    )
    if args.from_manifest:
        reports = asyncio.run(builder.reconstruct(load_manifest(args.manifest_path)))
        for report in reports:
            print(report.model_dump_json())
        return
    manifest = asyncio.run(builder.build())
    if args.offline and args.manifest_path.exists():
        # The cache does not remember source health: keep the last live build's reports.
        previous = {report.source: report for report in load_manifest(args.manifest_path).sources}
        manifest = manifest.model_copy(
            update={
                "sources": [
                    previous[r.source].model_copy(update={"selected": r.selected})
                    if r.source in previous
                    else r
                    for r in manifest.sources
                ]
            }
        )
    write_manifest(manifest, args.manifest_path)
    print(format_corpus_summary(manifest))
    print(f"manifest: {args.manifest_path}")


def _status(args: argparse.Namespace) -> None:
    if not args.manifest_path.exists():
        print(f"no manifest at {args.manifest_path}")
        raise SystemExit(EXIT_INFRASTRUCTURE_ERROR)
    manifest = load_manifest(args.manifest_path)
    print(format_corpus_summary(manifest))
    try:
        _, mismatched = load_replay_entries(
            RawCorpusCache(args.cache_dir), manifest.articles, SOURCES
        )
    except CorpusCacheMissError as exc:
        print(f"cache: incomplete (missing {exc}); run build-real-world-corpus --from-manifest")
        raise SystemExit(EXIT_INFRASTRUCTURE_ERROR) from None
    if mismatched:
        print(f"cache: {len(mismatched)} articles changed since the manifest: {mismatched[:5]}")
        raise SystemExit(EXIT_INFRASTRUCTURE_ERROR)
    print(f"cache: complete, {len(manifest.articles)} articles match their content hashes")


def format_corpus_summary(manifest: CorpusManifest) -> str:
    articles = manifest.articles
    lines = [
        f"dataset_version: {manifest.dataset_version}",
        f"manifest_hash: {manifest.content_fingerprint()}",
        f"period: {manifest.period_start} .. {manifest.period_end} (seed {manifest.sampling_seed})",
        f"articles: {len(articles)}",
    ]
    if articles:
        lines.append(
            f"published: {min(a.published_at for a in articles).date()} .. "
            f"{max(a.published_at for a in articles).date()}"
        )
    by_source = Counter(article.source for article in articles)
    lines.append("by source: " + ", ".join(f"{k}={v}" for k, v in sorted(by_source.items())))
    by_split = Counter(article.corpus_split.value for article in articles)
    lines.append("by period: " + ", ".join(f"{k}={v}" for k, v in sorted(by_split.items())))
    lines.append(f"evaluation sample: {sum(a.evaluation_sample for a in articles)}")
    groups = Counter(article.duplicate_group for article in articles)
    lines.append(f"duplicate groups with >1 article: {sum(1 for n in groups.values() if n > 1)}")
    tags = Counter(tag for article in articles for tag in article.sampling_tags)
    lines.append("tags: " + ", ".join(f"{k}={v}" for k, v in sorted(tags.items())))
    for report in manifest.sources:
        lines.append(
            f"source {report.source}: status={report.status.value}"
            f"{f' ({report.stop_reason})' if report.stop_reason else ''} "
            f"discovered={report.discovered} candidates={report.fetch_candidates} "
            f"fetched={report.fetched} cached={report.from_cache} in_period={report.in_period} "
            f"failed={report.fetch_failed} parse_failed={report.parse_failed} "
            f"selected={report.selected} http={report.http_status_counts}"
        )
    return "\n".join(lines)
