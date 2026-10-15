"""The one final evaluation of the frozen v2 pipeline on the holdout of 400.

    python -m evaluation.relevance.final_holdout_evaluation

Everything is taken as frozen and checked first: the pipeline of `frozen_pipeline_v2.json`
(the gate's source, the train it is fitted on, the threshold of the temporal CV), and the
holdout's v2 labels (`holdout_v2/final_labels_v2.jsonl`, audited). The classifier is
fitted on the development train as for the outer validation, the gate turns to false what
it rules out, and the holdout is judged once at the frozen threshold. A second run is
refused: the result file exists.
"""

from __future__ import annotations

import hashlib
import json
import sys
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evaluation.relevance import eligibility_gate
from evaluation.relevance.baseline import (
    Article,
    build_model,
    calculate_metrics,
    probabilities_of,
    texts_of,
)
from evaluation.relevance.court_relevance_baseline import load_split
from evaluation.relevance.holdout import read_jsonl, write_atomically
from evaluation.relevance.preprocessing import PREPROCESSORS

ROOT = Path(__file__).resolve().parents[3]
SPLIT_DIR = ROOT / "data/ml/relevance/court_monitor_v2"
CONFIG = SPLIT_DIR / "results/frozen_pipeline_v2.json"
HOLDOUT_DIR = ROOT / "data/ml/relevance/holdout_v2"
RESULT = HOLDOUT_DIR / "final_evaluation.json"
FIELD = "relevant_to_court_monitor"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def checked(config: Mapping[str, Any], gate_path: Path, train_path: Path) -> None:
    """Refuse a pipeline that is not the frozen one."""
    if _sha256(gate_path) != config["gate_source_sha256"]:
        raise ValueError("the gate changed since the pipeline was frozen")
    if _sha256(train_path) != config["train_sha256"]:
        raise ValueError("the train changed since the pipeline was frozen")


def holdout_articles(holdout_dir: Path) -> list[Article]:
    """The holdout's articles with their audited, frozen v2 labels."""
    metadata = json.loads((holdout_dir / "final_dataset_metadata_v2.json").read_text())
    labels_path = holdout_dir / "final_labels_v2.jsonl"
    if not metadata.get("frozen") or _sha256(labels_path) != metadata["final_labels_sha256"]:
        raise ValueError("the holdout labels are not the frozen ones")
    if "audit_corrections" not in metadata:
        raise ValueError("the holdout labels are not audited")
    labels = {int(r["article_id"]): r for r in read_jsonl(labels_path)}
    articles = read_jsonl(holdout_dir / "articles.jsonl")
    if set(labels) != {int(a["article_id"]) for a in articles}:
        raise ValueError("labels and articles differ")
    return [
        Article(
            article_id=int(a["article_id"]),
            title=str(a["title"]),
            text=str(a["text"]),
            published_at=datetime.fromisoformat(a["published_at"]),
            label=int(labels[int(a["article_id"])][FIELD]),
            kind=str(labels[int(a["article_id"])]["annotation_source"]),
            source=str(a["source"]),
            stratum="",
        )
        for a in articles
    ]


def evaluate(
    train: Sequence[Article],
    holdout: Sequence[Article],
    threshold: float,
    preprocess: Callable[[str], str],
    model_factory: Callable[[], Any] = build_model,
) -> dict[str, Any]:
    model = model_factory()
    model.fit(texts_of(train, preprocess), [r.label for r in train])
    raw = probabilities_of(model, holdout, preprocess)
    verdicts = [eligibility_gate.gate(r.title, r.text) for r in holdout]
    scores = [0.0 if v is not None else p for p, v in zip(raw, verdicts, strict=True)]
    labels = [r.label for r in holdout]
    metrics = {
        k: v
        for k, v in calculate_metrics(labels, scores, threshold).items()
        if k != "confusion_matrix"
    }
    classifier_only = calculate_metrics(labels, raw, threshold)

    def entry(
        row: Article, score: float, probability: float, verdict: str | None
    ) -> dict[str, Any]:
        return {
            "article_id": row.article_id,
            "score": score,
            "classifier_probability": probability,
            "gate": verdict,
            "title": row.title,
            "source": row.source,
            "annotation_source": row.kind,
            "snippet": " ".join(row.text.split())[:300],
        }

    rows = list(zip(holdout, scores, raw, verdicts, strict=True))
    fn = [entry(*x) for x in rows if x[0].label and x[1] < threshold]
    fp = [entry(*x) for x in rows if not x[0].label and x[1] >= threshold]
    fn.sort(key=lambda e: (-e["score"], e["article_id"]))
    fp.sort(key=lambda e: (-e["score"], e["article_id"]))
    return {
        "threshold": threshold,
        "total": len(holdout),
        "relevant": sum(labels),
        "metrics": metrics,
        "classifier_without_gate": {
            "average_precision": classifier_only["average_precision"],
            "roc_auc": classifier_only["roc_auc"],
            "tn": classifier_only["tn"],
            "fp": classifier_only["fp"],
            "fn": classifier_only["fn"],
            "tp": classifier_only["tp"],
        },
        "gate_dropped": sum(1 for v in verdicts if v),
        "gate_dropped_by_category": dict(sorted(Counter(v for v in verdicts if v).items())),
        "gate_relevant_dropped": [
            {"article_id": r.article_id, "category": v, "title": r.title}
            for r, v in zip(holdout, verdicts, strict=True)
            if v and r.label
        ],
        "false_negatives": fn,
        "false_positives_top20": fp[:20],
        "false_positive_count": len(fp),
    }


def main() -> int:
    if RESULT.exists():
        print(f"refused: {RESULT} exists; the holdout is judged once", file=sys.stderr)
        return 1
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    try:
        checked(config, Path(eligibility_gate.__file__), SPLIT_DIR / "train.jsonl")
        holdout = holdout_articles(HOLDOUT_DIR)
    except ValueError as error:
        print(f"refused: {error}", file=sys.stderr)
        return 1
    train = load_split(SPLIT_DIR / "train.jsonl")
    report = evaluate(train, holdout, config["threshold"], PREPROCESSORS[config["preprocessing"]])
    report = {
        "pipeline": {
            k: config[k] for k in ("rubric", "preprocessing", "classifier", "threshold", "gate")
        },
        "frozen_pipeline_sha256": _sha256(CONFIG),
        "holdout_labels_sha256": _sha256(HOLDOUT_DIR / "final_labels_v2.jsonl"),
        **report,
        "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }
    write_atomically(RESULT, json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(f"results: {RESULT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
