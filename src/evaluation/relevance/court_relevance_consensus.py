"""Final `relevant_to_court_monitor` labels of the sample: two LLMs, then a person.

    python -m evaluation.relevance.court_relevance_consensus

Where A and B give the same answer, that is the label (`llm_consensus`); their
explanations do not matter. Where they differ, or one gave none, a person decides
(`human_adjudication`, in `adjudications.jsonl`), and that always wins. An undecided
disagreement is in no final label. Once all are decided, 50 consensus labels drawn with a
fixed seed are checked by a person; at 90% agreement or more the silver labels are good
enough for a first experiment.

Only the files of `court_monitor_relevance/` are read: never the `kind` / `political` of
`junk-labels-v1`. Nothing here calls a model.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from evaluation.relevance.consensus import audit_sample
from evaluation.relevance.holdout import FrozenError, ordered, read_jsonl, write_atomically

ROOT = Path(__file__).resolve().parents[3]
HOLDOUT_DIR = ROOT / "data/ml/relevance/holdout"
OUT_DIR = HOLDOUT_DIR / "court_monitor_relevance"
FIELD = "relevant_to_court_monitor"
CONSENSUS = "llm_consensus"
ADJUDICATION = "human_adjudication"
# Allowed only where a dataset says so (the development corpus): a decision of the
# assistant, kept apart from a person's.
ASSISTANT_ADJUDICATION = "assistant_adjudication"
HUMAN_ONLY = (ADJUDICATION,)
ACCEPTABLE_AGREEMENT = 0.90


def by_article(rows: Sequence[Mapping[str, Any]], name: str) -> dict[int, dict[str, Any]]:
    labels: dict[int, dict[str, Any]] = {}
    for row in rows:
        article_id = int(row["article_id"])
        if article_id in labels:
            raise ValueError(f"{name}: {article_id} twice")
        if not isinstance(row.get(FIELD), bool):
            raise TypeError(f"{name}: {article_id} has no true/false {FIELD}")
        labels[article_id] = dict(row)
    return labels


def disagreement(a: Mapping[str, Any] | None, b: Mapping[str, Any] | None) -> str | None:
    """None when A and B give the same answer; else why not."""
    if a is None or b is None:
        return "missing"
    return None if a[FIELD] == b[FIELD] else "relevance"


def _label(article_id: int, relevant: bool, source: str) -> dict[str, Any]:
    return {"article_id": article_id, FIELD: relevant, "annotation_source": source}


def _shown(label: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if label is None:
        return None
    return {key: label.get(key) for key in (FIELD, "explanation", "model_id", "answered_by")}


def build(
    articles: Sequence[Mapping[str, Any]],
    labels_a: Mapping[int, Mapping[str, Any]],
    labels_b: Mapping[int, Mapping[str, Any]],
    adjudications: Mapping[int, Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int]]:
    """The final labels, the disagreements, and the counts."""
    known = {int(article["article_id"]) for article in articles}
    for name, labels in (("A", labels_a), ("B", labels_b), ("adjudications", adjudications)):
        stray = set(labels) - known
        if stray:
            raise ValueError(f"{name} labels articles outside the sample: {sorted(stray)[:5]}")
    final: list[dict[str, Any]] = []
    disagreements: list[dict[str, Any]] = []
    counts = dict.fromkeys(
        (
            "consensus",
            "consensus_relevant",
            "consensus_not_relevant",
            "relevance",
            "missing",
            "adjudicated",
            "remaining",
        ),
        0,
    )
    for article in ordered(articles):
        article_id = int(article["article_id"])
        a, b = labels_a.get(article_id), labels_b.get(article_id)
        differs = disagreement(a, b)
        if differs is None and a is not None:
            counts["consensus"] += 1
            counts["consensus_relevant" if a[FIELD] else "consensus_not_relevant"] += 1
        else:
            counts[str(differs)] += 1
            disagreements.append(
                {"article_id": article_id, "differs": differs, "a": _shown(a), "b": _shown(b)}
            )
        human = adjudications.get(article_id)
        if human is not None:
            counts["adjudicated"] += 1
            final.append(_label(article_id, bool(human[FIELD]), human["annotation_source"]))
        elif differs is None and a is not None:
            final.append(_label(article_id, bool(a[FIELD]), CONSENSUS))
        else:
            counts["remaining"] += 1
    counts["labelled_a"] = len(labels_a)
    counts["labelled_b"] = len(labels_b)
    counts["relevant_a"] = sum(1 for label in labels_a.values() if label[FIELD])
    counts["relevant_b"] = sum(1 for label in labels_b.values() if label[FIELD])
    return final, disagreements, counts


def _read(path: Path) -> list[dict[str, Any]]:
    return read_jsonl(path) if path.exists() else []


def _jsonl(rows: Sequence[Mapping[str, Any]]) -> str:
    return "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)


def rebuild(
    out_dir: Path, sample_path: Path, adjudication_sources: Sequence[str] = HUMAN_ONLY
) -> dict[str, int]:
    """Write `consensus_labels.jsonl` and `disagreements.jsonl`; the counts."""
    adjudications = by_article(_read(out_dir / "adjudications.jsonl"), "adjudications")
    for article_id, row in adjudications.items():
        if row.get("annotation_source") not in adjudication_sources:
            raise ValueError(
                f"adjudications: {article_id} is {row.get('annotation_source')!r}, "
                f"allowed {list(adjudication_sources)}"
            )
    final, disagreements, counts = build(
        read_jsonl(sample_path),
        by_article(_read(out_dir / "labels_a.jsonl"), "labels_a"),
        by_article(_read(out_dir / "labels_b.jsonl"), "labels_b"),
        adjudications,
    )
    write_atomically(out_dir / "consensus_labels.jsonl", _jsonl(final))
    write_atomically(out_dir / "disagreements.jsonl", _jsonl(disagreements))
    return counts


def freeze_audit(
    out_dir: Path, sample_path: Path, adjudication_sources: Sequence[str] = HUMAN_ONLY
) -> list[int]:
    """Draw the audit once every disagreement is decided; the drawn ids, frozen."""
    path = out_dir / "audit_sample.json"
    if path.exists():
        frozen: list[int] = json.loads(path.read_text(encoding="utf-8"))["article_ids"]
        return frozen
    counts = rebuild(out_dir, sample_path, adjudication_sources)
    if counts["remaining"]:
        raise ValueError(f"{counts['remaining']} disagreements are not decided yet")
    consensus = [
        int(row["article_id"])
        for row in read_jsonl(out_dir / "consensus_labels.jsonl")
        if row["annotation_source"] == CONSENSUS
    ]
    ids = audit_sample(consensus)
    if path.exists():
        raise FrozenError(f"{path.name} appeared meanwhile")
    write_atomically(
        path,
        json.dumps(
            {"seed": 20261015, "size": len(ids), "drawn_from": len(consensus), "article_ids": ids},
            indent=2,
        )
        + "\n",
    )
    return ids


def audit_agreement(verdicts: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    agree = sum(1 for verdict in verdicts if verdict["verdict"] == "agree")
    rate = agree / len(verdicts) if verdicts else None
    return {
        "checked": len(verdicts),
        "agree": agree,
        "disagree": len(verdicts) - agree,
        "agreement_rate": rate,
        "acceptable": None if rate is None else rate >= ACCEPTABLE_AGREEMENT,
    }


def main() -> int:
    print(json.dumps(rebuild(OUT_DIR, HOLDOUT_DIR / "sample_articles.jsonl")))
    audit = OUT_DIR / "audit.jsonl"
    if audit.exists():
        print(json.dumps(audit_agreement(read_jsonl(audit))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
