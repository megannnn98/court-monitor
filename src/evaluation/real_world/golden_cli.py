"""CLI handlers for the real-world golden dataset workflow."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evaluation.real_world.cli_helpers import EXIT_INFRASTRUCTURE_ERROR, corpus_texts
from evaluation.real_world.corpus_cache import RawCorpusCache
from evaluation.real_world.golden import (
    DEFAULT_GOLDEN_DIR,
    AnnotationStatus,
    GoldenArticle,
    GoldenDataset,
    load_golden_dataset,
    manifest_problems,
    text_problems,
    write_golden_dataset,
)
from evaluation.real_world.models import DEFAULT_CACHE_DIR, DEFAULT_MANIFEST_PATH, load_manifest


def add_golden_arguments(subparsers: Any) -> None:
    golden = subparsers.add_parser(
        "real-world-golden", help="Validate, review and verify the real-world golden dataset"
    )
    golden.add_argument("--golden-dir", type=Path, default=DEFAULT_GOLDEN_DIR)
    golden.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    golden.add_argument("--manifest-path", type=Path, default=DEFAULT_MANIFEST_PATH)
    commands = golden.add_subparsers(dest="golden_command", required=True)
    validate = commands.add_parser("validate", help="Schema, references, splits, offsets, hash")
    validate.add_argument(
        "--write-hash",
        action="store_true",
        help="Record the current content hash in VERSION.json (after bumping dataset_version)",
    )
    sheet = commands.add_parser("review-sheet", help="Markdown sheets for human verification")
    sheet.add_argument("--case-id", action="append", default=None)
    sheet.add_argument("--output-dir", type=Path, default=DEFAULT_CACHE_DIR / "review")
    verify = commands.add_parser("verify", help="Mark a human-checked DRAFT case VERIFIED")
    verify.add_argument("--case-id", required=True)
    verify.add_argument("--reviewer", required=True)
    verify.add_argument(
        "--confirm-checked-against-source",
        action="store_true",
        required=True,
        help="Required: you read the source article and checked every annotation",
    )
    locate = commands.add_parser("locate", help="Print a span (offsets) for text in an article")
    locate.add_argument("--key", required=True, help="source:external_id")
    locate.add_argument("--text", required=True)
    locate.add_argument("--occurrence", type=int, default=1)


def run_golden_command(args: argparse.Namespace) -> None:
    manifest = load_manifest(args.manifest_path)
    cache = RawCorpusCache(args.cache_dir)
    if args.golden_command == "locate":
        text = corpus_texts(cache, manifest)[args.key]
        start = -1
        for _ in range(args.occurrence):
            start = text.find(args.text, start + 1)
            if start < 0:
                raise SystemExit(f"{args.text!r} occurrence {args.occurrence} not found")
        print(
            json.dumps(
                {"start": start, "end": start + len(args.text), "text": args.text},
                ensure_ascii=False,
            )
        )
        return
    try:
        golden = load_golden_dataset(args.golden_dir)
    except (ValueError, OSError) as exc:
        print(f"golden dataset invalid: {exc}")
        raise SystemExit(EXIT_INFRASTRUCTURE_ERROR) from None
    texts = corpus_texts(cache, manifest)
    problems = manifest_problems(golden, manifest) + text_problems(golden, texts)
    if args.golden_command == "validate":
        current = golden.content_hash()
        recorded = golden.version.golden_dataset_hash
        if args.write_hash and not problems:
            write_golden_dataset(golden, args.golden_dir)
            recorded = current
        for problem in problems:
            print(f"problem: {problem}")
        drafts = sum(a.annotation_status is AnnotationStatus.DRAFT for a in golden.articles)
        print(
            f"dataset_version={golden.version.dataset_version} articles={len(golden.articles)} "
            f"draft={drafts} verified={len(golden.articles) - drafts} persons={len(golden.persons)}"
        )
        print(f"golden_dataset_hash={current} recorded={recorded}")
        if problems or current != recorded:
            raise SystemExit(EXIT_INFRASTRUCTURE_ERROR)
        return
    if args.golden_command == "review-sheet":
        wanted = set(args.case_id or [a.case_id for a in golden.articles])
        args.output_dir.mkdir(parents=True, exist_ok=True)
        for article in golden.articles:
            if article.case_id in wanted:
                path = args.output_dir / f"{article.case_id}.md"
                path.write_text(review_sheet(golden, article, texts.get(article.key)), "utf-8")
                print(path)
        return
    if args.golden_command == "verify":
        case_problems = [p for p in problems if p.startswith(f"{args.case_id}:")]
        if case_problems:
            raise SystemExit("cannot verify, fix first:\n" + "\n".join(case_problems))
        articles = []
        found = False
        for article in golden.articles:
            if article.case_id == args.case_id:
                found = True
                article = article.model_copy(
                    update={
                        "annotation_status": AnnotationStatus.VERIFIED,
                        "verified_by": args.reviewer,
                        "verified_at": datetime.now(UTC),
                    }
                )
                GoldenArticle.model_validate(article.model_dump())
            articles.append(article)
        if not found:
            raise SystemExit(f"unknown case {args.case_id}")
        updated = GoldenDataset(version=golden.version, persons=golden.persons, articles=articles)
        write_golden_dataset(updated, args.golden_dir)
        print(
            f"{args.case_id} VERIFIED by {args.reviewer}; golden_dataset_hash={updated.content_hash()}. "
            "Bump dataset_version in VERSION.json before publishing results."
        )


def review_sheet(golden: GoldenDataset, article: GoldenArticle, text: str | None) -> str:
    """Article/URL → persons → mentions → events → persecution → RF → candidate → evidence → disputes."""
    lines = [
        f"# Review {article.case_id} ({article.annotation_status.value}, {article.annotation_origin.value})",
        "",
        f"- URL: {article.canonical_url}",
        f"- source: {article.source}, published {article.published_at.isoformat()}, split {article.split.value}",
        f"- tags: {', '.join(article.tags) or '—'}",
        "",
        (
            "Check every line against the article; then run "
            f"`real-world-golden verify --case-id {article.case_id} --reviewer NAME --confirm-checked-against-source`."
        ),
        "",
        "## Persons",
        "",
    ]
    person_ids = sorted({m.golden_person_id for m in article.mentions})
    for pid in person_ids:
        person = golden.person(pid)
        lines.append(f"### {person.canonical_name} (`{pid}`)")
        lines.append("")
        lines.append(
            "Mentions: "
            + "; ".join(
                f"«{m.text}» @{m.start}" for m in article.mentions if m.golden_person_id == pid
            )
        )
        if person.aliases or person.same_as or person.distinct_from:
            lines.append(
                f"Aliases {person.aliases}; same_as {person.same_as}; distinct_from {person.distinct_from}"
            )
        events = [e for e in article.events if pid in e.person_ids]
        for event in events:
            lines.append(
                f"- [ ] event `{event.event_id}` {event.event_type.value}"
                f"{' shared' if event.shared else ''}{' historical' if event.historical else ''}: «{event.evidence.text}»"
            )
        if person.persecution:
            lines.append(
                f"- [ ] persecution: {person.persecution.expected_status.value} "
                f"(also acceptable {[s.value for s in person.persecution.acceptable_statuses]}) — {person.persecution.notes or ''}"
            )
            for ref in person.persecution.evidence:
                lines.append(f"  - evidence [{ref.case_id}] «{ref.span.text}»")
        if person.rosfinmonitoring:
            rf = person.rosfinmonitoring
            lines.append(
                f"- [ ] RF ({rf.snapshot}): {rf.expected_status.value}, entry {rf.expected_entry!r}, review acceptable {rf.acceptable_review}"
            )
        if person.candidate:
            lines.append(
                f"- [ ] main candidate (POLITICAL + NOT_MATCHED): {person.candidate.expected_main_candidate}"
            )
        lines.append("")
    lines += ["## Disputed / open questions", ""]
    lines += [f"- {item}" for item in article.disputed] or ["- none"]
    if article.known_limitation:
        lines += [
            "",
            f"Known limitation: {article.known_limitation} ({[k.value for k in article.known_limitation_kinds]})",
        ]
    lines += ["", "## Article text (local cache, not committed)", ""]
    if text is None:
        lines.append("_not in the local cache_")
    else:
        marked = text
        for mention in sorted(article.mentions, key=lambda m: m.start, reverse=True):
            marked = f"{marked[: mention.start]}**[{marked[mention.start : mention.end]}|{mention.golden_person_id}]**{marked[mention.end :]}"
        lines.append(marked)
    return "\n".join(lines) + "\n"
