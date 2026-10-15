"""The sample's final labels from two LLM annotators and a person.

    python -m evaluation.relevance.consensus

Where A and B agree on both `kind` and `political`, that is the label
(`llm_consensus`). Where they differ — on either field, or one of them gave nothing — the
article waits in `annotation_disagreements.jsonl` for a person; no third model decides.
A person's decision (`human_adjudication`, kept in `adjudications.jsonl`) always wins.
`sample_labels.jsonl` is rebuilt from these files and holds nothing else.

After every disagreement is decided, 50 consensus labels, drawn with a fixed seed, are
checked by a person (`consensus_audit.jsonl`): agree, or disagree with the right label.
The consensus label itself is never changed by the audit.

Nothing here calls a model.
"""

from __future__ import annotations

import json
import random
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from evaluation.relevance.holdout import KINDS, FrozenError, ordered, read_jsonl, write_atomically

ROOT = Path(__file__).resolve().parents[3]
HOLDOUT_DIR = ROOT / "data/ml/relevance/holdout"
AUDIT_SIZE = 50
AUDIT_SEED = 20261015

CONSENSUS = "llm_consensus"
ADJUDICATION = "human_adjudication"


def _decision(label: Mapping[str, Any]) -> tuple[str, bool]:
    return str(label["kind"]), bool(label["political"])


def checked(label: Mapping[str, Any], where: str) -> dict[str, Any]:
    """A label's `kind` and `political`, by the rubric: any kind may be political."""
    if label["kind"] not in KINDS:
        raise ValueError(f"{where}: unknown kind {label['kind']!r}")
    if not isinstance(label["political"], bool):
        raise TypeError(f"{where}: political is not true/false")
    return dict(label)


def by_article(rows: Sequence[Mapping[str, Any]], name: str) -> dict[int, dict[str, Any]]:
    labels: dict[int, dict[str, Any]] = {}
    for row in rows:
        article_id = int(row["article_id"])
        if article_id in labels:
            raise ValueError(f"{name}: {article_id} twice")
        labels[article_id] = checked(row, f"{name}: {article_id}")
    return labels


def disagreement(a: Mapping[str, Any] | None, b: Mapping[str, Any] | None) -> str | None:
    """None when A and B agree on kind and political; else what they differ on."""
    if a is None or b is None:
        return "missing"
    if a["kind"] != b["kind"]:
        return "kind"
    if a["political"] != b["political"]:
        return "political_only"
    return None


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
        ("consensus", "kind", "political_only", "missing", "adjudicated", "remaining"), 0
    )
    for article in ordered(articles):
        article_id = int(article["article_id"])
        a, b = labels_a.get(article_id), labels_b.get(article_id)
        differs = disagreement(a, b)
        if differs is None:
            counts["consensus"] += 1
        else:
            counts[differs] += 1
            disagreements.append(
                {
                    "article_id": article_id,
                    "differs": differs,
                    "a": _shown(a),
                    "b": _shown(b),
                }
            )
        human = adjudications.get(article_id)
        if human is not None:
            counts["adjudicated"] += 1
            kind, political = _decision(human)
            final.append(_label(article_id, kind, political, ADJUDICATION))
        elif differs is None and a is not None:
            kind, political = _decision(a)
            final.append(_label(article_id, kind, political, CONSENSUS))
        else:
            counts["remaining"] += 1
    counts["labelled_a"] = len(labels_a)
    counts["labelled_b"] = len(labels_b)
    return final, disagreements, counts


def _shown(label: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if label is None:
        return None
    return {
        key: label.get(key)
        for key in ("kind", "political", "explanation", "model_id", "answered_by")
    }


def _label(article_id: int, kind: str, political: bool, source: str) -> dict[str, Any]:
    return {
        "article_id": article_id,
        "kind": kind,
        "political": political,
        "annotation_source": source,
    }


def _jsonl(rows: Sequence[Mapping[str, Any]]) -> str:
    return "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)


def _read(path: Path) -> list[dict[str, Any]]:
    return read_jsonl(path) if path.exists() else []


def rebuild(holdout_dir: Path) -> dict[str, int]:
    """Write `sample_labels.jsonl` and `annotation_disagreements.jsonl` from A, B and the
    adjudications; the counts."""
    articles = read_jsonl(holdout_dir / "sample_articles.jsonl")
    adjudications = by_article(_read(holdout_dir / "adjudications.jsonl"), "adjudications")
    for article_id, row in adjudications.items():
        if row.get("annotation_source") != ADJUDICATION:
            raise ValueError(f"adjudications: {article_id} is not a human adjudication")
    final, disagreements, counts = build(
        articles,
        by_article(_read(holdout_dir / "labels_a.jsonl"), "labels_a"),
        by_article(_read(holdout_dir / "labels_b.jsonl"), "labels_b"),
        adjudications,
    )
    write_atomically(holdout_dir / "sample_labels.jsonl", _jsonl(final))
    write_atomically(holdout_dir / "annotation_disagreements.jsonl", _jsonl(disagreements))
    return counts


def audit_sample(
    consensus_ids: Sequence[int], size: int = AUDIT_SIZE, seed: int = AUDIT_SEED
) -> list[int]:
    """`size` consensus articles, uniformly, by `seed`, of the ids in ascending order."""
    population = sorted(set(consensus_ids))
    if len(population) < size:
        raise ValueError(f"{len(population)} consensus labels, fewer than {size}")
    return sorted(random.Random(seed).sample(population, size))


def freeze_audit(holdout_dir: Path) -> list[int]:
    """Draw the audit once every disagreement is decided; the drawn ids, frozen."""
    path = holdout_dir / "consensus_audit_sample.json"
    if path.exists():
        frozen: list[int] = json.loads(path.read_text(encoding="utf-8"))["article_ids"]
        return frozen
    counts = rebuild(holdout_dir)
    if counts["remaining"]:
        raise ValueError(f"{counts['remaining']} disagreements are not decided yet")
    consensus = [
        int(row["article_id"])
        for row in read_jsonl(holdout_dir / "sample_labels.jsonl")
        if row["annotation_source"] == CONSENSUS
    ]
    ids = audit_sample(consensus)
    if path.exists():
        raise FrozenError(f"{path.name} appeared meanwhile")
    write_atomically(
        path,
        json.dumps(
            {
                "seed": AUDIT_SEED,
                "size": AUDIT_SIZE,
                "drawn_from": len(consensus),
                "article_ids": ids,
            },
            indent=2,
        )
        + "\n",
    )
    return ids


def audit_agreement(verdicts: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    agree = sum(1 for verdict in verdicts if verdict["verdict"] == "agree")
    return {
        "checked": len(verdicts),
        "agree": agree,
        "disagree": len(verdicts) - agree,
        "agreement_rate": agree / len(verdicts) if verdicts else None,
    }


def main() -> int:
    counts = rebuild(HOLDOUT_DIR)
    print(json.dumps(counts))
    audit = HOLDOUT_DIR / "consensus_audit.jsonl"
    if audit.exists():
        print(json.dumps(audit_agreement(read_jsonl(audit))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
