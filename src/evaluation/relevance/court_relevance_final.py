"""The frozen evaluation set of `court-monitor-relevance-v1`: one label per sample article.

    python -m evaluation.relevance.court_relevance_final

A person's word comes first: an audit correction over a disagreement decided by hand over
the LLM consensus. The set is checked whole — every sample article exactly once, no
disagreement left, every audit correction in it — and then frozen: a second build
changes nothing, and refuses if it would come out different.

No model is called; the labels are only read.
"""

from __future__ import annotations

import hashlib
import json
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evaluation.relevance.court_relevance_consensus import (
    ADJUDICATION,
    ASSISTANT_ADJUDICATION,
    CONSENSUS,
    FIELD,
    HOLDOUT_DIR,
    HUMAN_ONLY,
    OUT_DIR,
    audit_agreement,
    rebuild,
)
from evaluation.relevance.court_relevance_rubric import PROMPT_VERSION
from evaluation.relevance.holdout import FrozenError, ordered, read_jsonl, write_atomically
from evaluation.relevance.sample_holdout import check_frozen

AUDIT_CORRECTION = "human_audit_correction"
# An audit by the assistant, allowed only where a dataset says so (the development
# corpus); a verdict without `annotation_source` is a person's.
HUMAN_AUDIT = "human_audit"
ASSISTANT_AUDIT = "assistant_audit"
ASSISTANT_AUDIT_CORRECTION = "assistant_audit_correction"
HUMAN_AUDIT_ONLY = (HUMAN_AUDIT,)
CORRECTION_OF = {HUMAN_AUDIT: AUDIT_CORRECTION, ASSISTANT_AUDIT: ASSISTANT_AUDIT_CORRECTION}
SOURCES = (CONSENSUS, ADJUDICATION, ASSISTANT_ADJUDICATION, AUDIT_CORRECTION)
LABELS = "final_labels.jsonl"
METADATA = "final_dataset_metadata.json"


def final_labels(
    articles: Sequence[Mapping[str, Any]],
    consensus_labels: Sequence[Mapping[str, Any]],
    audit: Sequence[Mapping[str, Any]],
    adjudication_sources: Sequence[str] = HUMAN_ONLY,
    audit_sources: Sequence[str] = HUMAN_AUDIT_ONLY,
) -> list[dict[str, Any]]:
    """One label per article, in publication order: an audit correction, else the label
    `consensus_labels` holds (a person's decision there already outranks the consensus).
    Refuses a set that is not whole."""
    by_id = {int(article["article_id"]) for article in articles}
    labels: dict[int, Mapping[str, Any]] = {}
    for row in consensus_labels:
        article_id = int(row["article_id"])
        if article_id in labels:
            raise ValueError(f"{article_id} is labelled twice")
        if row["annotation_source"] not in (CONSENSUS, *adjudication_sources):
            raise ValueError(f"{article_id}: unknown source {row['annotation_source']!r}")
        labels[article_id] = row
    corrections: dict[int, tuple[bool, str]] = {}
    for verdict in audit:
        article_id = int(verdict["article_id"])
        auditor = verdict.get("annotation_source", HUMAN_AUDIT)
        if auditor not in audit_sources:
            raise ValueError(f"audit of {article_id} by {auditor!r}, allowed {list(audit_sources)}")
        answer = verdict["human"] if "human" in verdict else verdict["reviewer"]
        held = labels.get(article_id)
        if held is None or held["annotation_source"] != CONSENSUS:
            raise ValueError(f"audit of {article_id}, which has no consensus label")
        if verdict["consensus"] != held[FIELD]:
            raise ValueError(f"audit of {article_id} saw another consensus than the labels")
        agrees = verdict["verdict"] == "agree"
        if agrees != (answer == verdict["consensus"]):
            raise ValueError(f"audit of {article_id}: verdict and answers disagree")
        if not agrees:
            corrections[article_id] = (bool(answer), CORRECTION_OF[auditor])
    missing = by_id - set(labels)
    if missing:
        raise ValueError(f"{len(missing)} articles without a label, e.g. {sorted(missing)[:5]}")
    stray = set(labels) - by_id
    if stray:
        raise ValueError(f"labels of articles outside the sample: {sorted(stray)[:5]}")
    result = []
    for article in ordered(articles):
        article_id = int(article["article_id"])
        if article_id in corrections:
            relevant, source = corrections[article_id]
        else:
            relevant, source = (
                bool(labels[article_id][FIELD]),
                labels[article_id]["annotation_source"],
            )
        result.append({"article_id": article_id, FIELD: relevant, "annotation_source": source})
    return result


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _jsonl(rows: Sequence[Mapping[str, Any]]) -> str:
    return "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)


def freeze(out_dir: Path, holdout_dir: Path, *, created_at: datetime) -> dict[str, Any]:
    """The holdout's sample, frozen: `freeze_labels` of its labels."""
    sample = check_frozen(holdout_dir)
    return freeze_labels(
        out_dir,
        holdout_dir / "sample_articles.jsonl",
        dataset="court-monitor-relevance-v1-holdout",
        source={"sample_sha256": sample["sample_sha256"]},
        created_at=created_at,
    )


def freeze_labels(
    out_dir: Path,
    sample_path: Path,
    *,
    dataset: str,
    source: Mapping[str, Any],
    created_at: datetime,
    adjudication_sources: Sequence[str] = HUMAN_ONLY,
    audit_sources: Sequence[str] = HUMAN_AUDIT_ONLY,
) -> dict[str, Any]:
    """Build, check and freeze the final labels of the articles in `sample_path` and
    their metadata; the metadata. A frozen set is returned as it is if a build comes out
    the same, refused if not."""
    counts = rebuild(out_dir, sample_path, adjudication_sources)
    if counts["remaining"]:
        raise ValueError(f"{counts['remaining']} disagreements are not decided yet")
    audit = read_jsonl(out_dir / "audit.jsonl")
    audit_ids = json.loads((out_dir / "audit_sample.json").read_text(encoding="utf-8"))
    if sorted(int(v["article_id"]) for v in audit) != sorted(audit_ids["article_ids"]):
        raise ValueError("the audit is not complete")
    labels = final_labels(
        read_jsonl(sample_path),
        read_jsonl(out_dir / "consensus_labels.jsonl"),
        audit,
        adjudication_sources,
        audit_sources,
    )
    body = _jsonl(labels)
    labels_path, metadata_path = out_dir / LABELS, out_dir / METADATA
    if labels_path.exists() or metadata_path.exists():
        if not (labels_path.exists() and metadata_path.exists()):
            raise FrozenError("half a frozen set: one of the final files is missing")
        if labels_path.read_text(encoding="utf-8") != body:
            raise FrozenError(f"{LABELS} is frozen and a build now differs; nothing changed")
        frozen: dict[str, Any] = json.loads(metadata_path.read_text(encoding="utf-8"))
        if frozen["final_labels_sha256"] != _sha256(labels_path):
            raise FrozenError(f"{LABELS} changed since it was frozen")
        return frozen
    correction_sources = [CORRECTION_OF[source] for source in audit_sources]
    corrections = [row for row in labels if row["annotation_source"] in correction_sources]
    disagreeing = [v for v in audit if v["verdict"] == "disagree"]
    if len(corrections) != len(disagreeing):
        raise ValueError("an audit disagreement is not in the final labels")
    run = json.loads((out_dir / "annotation_run.json").read_text(encoding="utf-8"))
    write_atomically(labels_path, body)
    sources = Counter(row["annotation_source"] for row in labels)
    relevant = sum(1 for row in labels if row[FIELD])
    agreement = audit_agreement(audit)
    metadata = {
        "dataset": dataset,
        "prompt_version": PROMPT_VERSION,
        "article_count": len(labels),
        "relevant": relevant,
        "not_relevant": len(labels) - relevant,
        "sources": {
            source: sources.get(source, 0)
            for source in (CONSENSUS, *adjudication_sources, *correction_sources)
        },
        "priority": [*correction_sources, *adjudication_sources, CONSENSUS],
        "ab_agreement": {
            "agree": counts["consensus"],
            "of": len(labels),
            "rate": counts["consensus"] / len(labels),
        },
        "audit": {
            "annotation_source": sorted(
                {verdict.get("annotation_source", HUMAN_AUDIT) for verdict in audit}
            ),
            "size": len(audit),
            "agree": agreement["agree"],
            "rate": agreement["agreement_rate"],
            "seed": audit_ids["seed"],
            "corrected_article_ids": sorted(int(row["article_id"]) for row in corrections),
        },
        "model_a": run["model_a"],
        "model_b": run["model_b"],
        **source,
        "final_labels_sha256": _sha256(labels_path),
        "frozen": True,
        "created_at": created_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
    }
    write_atomically(metadata_path, json.dumps(metadata, ensure_ascii=False, indent=2) + "\n")
    return metadata


def main() -> int:
    try:
        metadata = freeze(OUT_DIR, HOLDOUT_DIR, created_at=datetime.now(UTC))
    except (FrozenError, ValueError) as error:
        print(f"refused: {error}", file=sys.stderr)
        return 1
    print(json.dumps(metadata, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
