"""The development corpus of `court-monitor-relevance-v1`: the old 2000 articles,
labelled anew.

    python -m evaluation.relevance.court_relevance_dev prepare
    python -m evaluation.relevance.court_relevance_dev annotate a|b
    python -m evaluation.relevance.court_relevance_dev consensus
    python -m evaluation.relevance.court_relevance_dev freeze
    python -m evaluation.relevance.court_relevance_dev split

`prepare` takes from the old train and test files only each article's text and metadata
— never its `kind`, `political`, `stratum` or label — and leaves out, before any model
sees it, every article the frozen holdout holds: the same id, the same text, or the same
text once normalized. Equal titles are only counted. Many leaks stop it: something is
then wrong, and nothing is written. The articles are frozen like the holdout's sample.

`annotate` and `consensus` are those of the holdout: the same rubric, models, batches
and rules, into `court_monitor_dev/`. `freeze` freezes the final labels as the holdout's
were (a person first, 90% audit agreement required); `split` cuts them by time into
`court_monitor/train.jsonl` and `validation.jsonl`.
"""

from __future__ import annotations

import hashlib
import json
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evaluation.relevance.court_relevance_consensus import (
    ADJUDICATION,
    ASSISTANT_ADJUDICATION,
    rebuild,
)
from evaluation.relevance.holdout import (
    FrozenError,
    ordered,
    read_jsonl,
    text_hash,
    write_atomically,
)

ROOT = Path(__file__).resolve().parents[3]
OLD_FILES = (ROOT / "data/ml/relevance/train.jsonl", ROOT / "data/ml/relevance/test.jsonl")
HOLDOUT_DIR = ROOT / "data/ml/relevance/holdout"
DEV_DIR = ROOT / "data/ml/relevance/court_monitor_dev"
SPLIT_DIR = ROOT / "data/ml/relevance/court_monitor"
# The development corpus alone may hold decisions of the assistant, never named a person's.
DEV_ADJUDICATION_SOURCES = (ADJUDICATION, ASSISTANT_ADJUDICATION)
# So may its audit: a person's or the assistant's.
DEV_AUDIT_SOURCES = ("human_audit", "assistant_audit")
KEPT_FIELDS = ("article_id", "title", "text", "source", "url", "published_at")
# More leaks than this share of the candidates is not an overlap but a mistake.
MAX_LEAK_SHARE = 0.05


def candidates(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Each article's text and metadata, and nothing of its old labels."""
    seen: set[int] = set()
    result = []
    for row in rows:
        article_id = int(row["article_id"])
        if article_id in seen:
            raise ValueError(f"candidate {article_id} twice")
        seen.add(article_id)
        result.append(
            {**{key: row[key] for key in KEPT_FIELDS}, "text_hash": text_hash(row["text"])}
        )
    return ordered(result)


def without_holdout(
    rows: Sequence[Mapping[str, Any]], holdout: Sequence[Mapping[str, Any]]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """The candidates the holdout does not hold, and what was left out and why."""
    ids = {int(a["article_id"]) for a in holdout}
    texts = {a["text"] for a in holdout}
    hashes = {text_hash(a["text"]) for a in holdout}
    titles = {a["title"].strip() for a in holdout}
    by_id = [int(r["article_id"]) for r in rows if int(r["article_id"]) in ids]
    by_text = [int(r["article_id"]) for r in rows if r["text"] in texts]
    by_hash = [int(r["article_id"]) for r in rows if text_hash(r["text"]) in hashes]
    out = set(by_id) | set(by_text) | set(by_hash)
    kept = [dict(r) for r in rows if int(r["article_id"]) not in out]
    report = {
        "candidate_count": len(rows),
        "excluded_by_article_id": len(by_id),
        "excluded_by_exact_text": len(by_text),
        "excluded_by_normalized_hash": len(by_hash),
        "excluded": len(out),
        "final_dev_candidate_count": len(kept),
        "duplicate_titles_not_excluded": sum(1 for r in kept if r["title"].strip() in titles),
        "excluded_article_ids": sorted(out),
    }
    return kept, report


def prepare(
    old_files: Sequence[Path], holdout_dir: Path, dev_dir: Path, *, created_at: datetime
) -> dict[str, Any]:
    """Write the frozen development articles; the leak report. Refuses to overwrite."""
    articles_path, metadata_path = dev_dir / "articles.jsonl", dev_dir / "metadata.json"
    if articles_path.exists() or metadata_path.exists():
        raise FrozenError(f"{dev_dir} is frozen; nothing changed")
    rows = candidates([row for path in old_files for row in read_jsonl(path)])
    holdout = read_jsonl(holdout_dir / "sample_articles.jsonl")
    final = read_jsonl(holdout_dir / "court_monitor_relevance" / "final_labels.jsonl")
    if {int(a["article_id"]) for a in holdout} != {int(r["article_id"]) for r in final}:
        raise ValueError("the frozen holdout's labels are not those of its sample")
    kept, report = without_holdout(rows, holdout)
    if report["excluded"] > MAX_LEAK_SHARE * report["candidate_count"]:
        raise ValueError(f"{report['excluded']} candidates are in the holdout; stopped")
    dev_dir.mkdir(parents=True, exist_ok=True)
    write_atomically(articles_path, "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in kept))
    published = [r["published_at"] for r in kept]
    metadata = {
        "dataset": "court-monitor-relevance-v1-dev",
        "sources": [
            str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)
            for path in old_files
        ],
        "fields_taken": list(KEPT_FIELDS),
        "old_labels_used": False,
        "leakage": report,
        "article_count": len(kept),
        "first_published_at": min(published),
        "last_published_at": max(published),
        "holdout_sample_sha256": hashlib.sha256(
            (holdout_dir / "sample_articles.jsonl").read_bytes()
        ).hexdigest(),
        "articles_sha256": hashlib.sha256(articles_path.read_bytes()).hexdigest(),
        "frozen": True,
        "created_at": created_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
    }
    write_atomically(metadata_path, json.dumps(metadata, ensure_ascii=False, indent=2) + "\n")
    return report


def checked_articles(dev_dir: Path) -> tuple[list[dict[str, Any]], str]:
    """The development articles, once checked to be those frozen; their sha256."""
    metadata = json.loads((dev_dir / "metadata.json").read_text(encoding="utf-8"))
    digest = hashlib.sha256((dev_dir / "articles.jsonl").read_bytes()).hexdigest()
    if digest != metadata["articles_sha256"]:
        raise ValueError("articles.jsonl changed since it was frozen")
    return read_jsonl(dev_dir / "articles.jsonl"), digest


def _freeze() -> int:
    from evaluation.relevance.court_relevance_consensus import audit_agreement
    from evaluation.relevance.court_relevance_final import freeze_labels

    _, digest = checked_articles(DEV_DIR)
    if not (DEV_DIR / "audit.jsonl").exists():
        print("refused: no audit yet (decide the disagreements, then audit)", file=sys.stderr)
        return 1
    agreement = audit_agreement(read_jsonl(DEV_DIR / "audit.jsonl"))
    if not agreement["acceptable"]:
        print(f"refused: audit agreement {agreement['agreement_rate']}", file=sys.stderr)
        return 1
    try:
        metadata = freeze_labels(
            DEV_DIR,
            DEV_DIR / "articles.jsonl",
            dataset="court-monitor-relevance-v1-dev",
            source={"articles_sha256": digest},
            created_at=datetime.now(UTC),
            adjudication_sources=DEV_ADJUDICATION_SOURCES,
            audit_sources=DEV_AUDIT_SOURCES,
        )
    except (FrozenError, ValueError) as error:
        print(f"refused: {error}", file=sys.stderr)
        return 1
    print(json.dumps(metadata, ensure_ascii=False, indent=2))
    return 0


def _split() -> int:
    from evaluation.relevance.court_relevance_split import labelled, split, write_split

    articles, digest = checked_articles(DEV_DIR)
    if not (DEV_DIR / "final_dataset_metadata.json").exists():
        print("refused: the development labels are not frozen yet", file=sys.stderr)
        return 1
    frozen = json.loads((DEV_DIR / "final_dataset_metadata.json").read_text(encoding="utf-8"))
    labels_path = DEV_DIR / "final_labels.jsonl"
    if hashlib.sha256(labels_path.read_bytes()).hexdigest() != frozen["final_labels_sha256"]:
        print("refused: final_labels.jsonl changed since it was frozen", file=sys.stderr)
        return 1
    train, validation, decided = split(labelled(articles, read_jsonl(labels_path)))
    try:
        metadata = write_split(
            SPLIT_DIR,
            train,
            validation,
            decided,
            {"articles_sha256": digest, "final_labels_sha256": frozen["final_labels_sha256"]},
            created_at=datetime.now(UTC),
        )
    except FrozenError as error:
        print(f"refused: {error}", file=sys.stderr)
        return 1
    print(json.dumps(metadata, ensure_ascii=False, indent=2))
    if metadata["validation_relevant_below_minimum"]:
        print("validation holds fewer than 50 relevant articles: stop before training")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args == ["prepare"]:
        try:
            report = prepare(OLD_FILES, HOLDOUT_DIR, DEV_DIR, created_at=datetime.now(UTC))
        except (FrozenError, ValueError) as error:
            print(f"refused: {error}", file=sys.stderr)
            return 1
        print(json.dumps({k: v for k, v in report.items() if k != "excluded_article_ids"}))
        return 0
    if len(args) == 2 and args[0] == "annotate" and args[1] in ("a", "b"):
        from evaluation.relevance.court_relevance import run_annotator

        articles, digest = checked_articles(DEV_DIR)
        return run_annotator(args[1], articles, DEV_DIR, {"articles_sha256": digest})
    if args == ["freeze"]:
        return _freeze()
    if args == ["split"]:
        return _split()
    if args == ["consensus"]:
        checked_articles(DEV_DIR)
        print(json.dumps(rebuild(DEV_DIR, DEV_DIR / "articles.jsonl", DEV_ADJUDICATION_SOURCES)))
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
