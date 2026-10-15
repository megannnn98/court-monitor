"""The eligibility gate before the frozen relevance baseline, against the baseline alone.

    python -m evaluation.relevance.gate_experiment

The baseline is frozen: lemma preprocessing, the TF-IDF + LogisticRegression of
`baseline.build_model` and the threshold chosen by the temporal CV
(`court_monitor_v2/results/temporal_cv.json`). The gate only turns to false the articles it
rules out; nothing is refitted or re-thresholded for it.

The comparison runs on the same temporal folds inside the development train. The outer
validation is read only when asked (`outer=True`) and only if the gate raises the pooled
out-of-fold precision, keeps the recall and rules out no relevant article there. Since gate
v1 missed article 1040 there, the outer validation is a development observation for the
gate, no longer an independent evaluation: the command does not read it.

    python -m evaluation.relevance.gate_experiment freeze

writes the frozen pipeline — rubric, preprocessing, classifier, threshold, gate — with the
sha256 of what it was built from.

The frozen holdout is never read.
"""

from __future__ import annotations

import hashlib
import json
import sys
from collections import Counter
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evaluation.relevance.analysis import point_metrics
from evaluation.relevance.baseline import Article, build_model, probabilities_of, texts_of
from evaluation.relevance.court_relevance_baseline import guarded, load_split
from evaluation.relevance.court_relevance_cv import SPLIT_DIR, temporal_folds, text_hashes
from evaluation.relevance.eligibility_gate import gate
from evaluation.relevance.holdout import write_atomically
from evaluation.relevance.preprocessing import lemmatize

RESULTS_DIR = SPLIT_DIR / "results"
FROZEN = RESULTS_DIR / "temporal_cv.json"


def gated(rows: Sequence[Article]) -> list[str | None]:
    return [gate(row.title, row.text) for row in rows]


def compare(
    rows: Sequence[Article],
    probabilities: Sequence[float],
    threshold: float,
) -> dict[str, Any]:
    """The baseline and the gate before it on the same predictions."""
    labels = [row.label for row in rows]
    verdicts = gated(rows)
    with_gate = [0.0 if v is not None else p for p, v in zip(probabilities, verdicts, strict=True)]
    base = point_metrics(labels, probabilities, threshold)
    after = point_metrics(labels, with_gate, threshold)
    dropped = [(row, v, p) for row, v, p in zip(rows, verdicts, probabilities, strict=True) if v]
    relevant_dropped = [
        {"article_id": row.article_id, "category": v, "title": row.title}
        for row, v, _ in dropped
        if row.label
    ]
    removed_fp = Counter(v for row, v, p in dropped if not row.label and p >= threshold)
    relevant = sum(labels)
    return {
        "total": len(rows),
        "relevant": relevant,
        "gate_dropped": len(dropped),
        "gate_dropped_by_category": dict(sorted(Counter(v for _, v, _ in dropped).items())),
        "relevant_dropped": relevant_dropped,
        "gate_recall": (relevant - len(relevant_dropped)) / relevant if relevant else 0.0,
        "baseline": base,
        "gate_baseline": after,
        "fp_removed_by_category": dict(sorted(removed_fp.items())),
    }


def improves(result: dict[str, Any]) -> bool:
    """Higher precision, recall not lower, and no relevant article ruled out."""
    base, after = result["baseline"], result["gate_baseline"]
    return (
        not result["relevant_dropped"]
        and after["precision_case"] > base["precision_case"]
        and after["recall_case"] >= base["recall_case"]
    )


def run(
    train_path: Path,
    validation_path: Path,
    threshold: float,
    preprocess: Callable[[str], str] = lemmatize,
    model_factory: Callable[[], Any] = build_model,
    outer: bool = False,
) -> dict[str, Any]:
    train = load_split(train_path)
    folds = temporal_folds(train, text_hashes(train_path))
    per_fold = []
    pooled_rows: list[Article] = []
    pooled: list[float] = []
    for fold_train, fold_validation, info in folds:
        model = model_factory()
        model.fit(texts_of(fold_train, preprocess), [r.label for r in fold_train])
        probabilities = probabilities_of(model, fold_validation, preprocess)
        per_fold.append(
            {"fold": info["fold"], **compare(fold_validation, probabilities, threshold)}
        )
        pooled_rows += fold_validation
        pooled += probabilities
    result = compare(pooled_rows, pooled, threshold)
    report: dict[str, Any] = {
        "threshold": threshold,
        "preprocessing": "lemma",
        "folds": per_fold,
        "pooled": result,
        "gate_improves_cv": improves(result),
        "holdout_read": False,
    }
    if not (outer and report["gate_improves_cv"]):
        return report
    # The outer validation is read only now, once, with everything frozen.
    validation = load_split(validation_path)
    model = model_factory()
    model.fit(texts_of(train, preprocess), [r.label for r in train])
    probabilities = probabilities_of(model, validation, preprocess)
    evaluation = compare(validation, probabilities, threshold)
    verdicts = gated(validation)
    evaluation["fp_removed"] = [
        {"article_id": row.article_id, "category": v, "title": row.title}
        for row, v, p in zip(validation, verdicts, probabilities, strict=True)
        if v and not row.label and p >= threshold
    ]
    report["outer_validation"] = evaluation
    return report


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def freeze(created_at: datetime) -> dict[str, Any]:
    """The frozen pipeline and the sha256 of what it was built from; refuses to overwrite."""
    from evaluation.relevance import eligibility_gate
    from evaluation.relevance.court_relevance_migration import DEV_DIR, LABELS
    from evaluation.relevance.person_criminal_case_rubric import PROMPT_VERSION

    out = guarded(RESULTS_DIR / "frozen_pipeline_v2.json")
    if out.exists():
        raise FileExistsError(f"{out} is frozen; nothing changed")
    cv = json.loads(guarded(FROZEN).read_text(encoding="utf-8"))
    experiment = guarded(RESULTS_DIR / "gate_experiment.json")
    report = json.loads(experiment.read_text(encoding="utf-8"))
    if not report["gate_improves_cv"] or report["pooled"]["relevant_dropped"]:
        raise ValueError("the gate did not pass the temporal CV; nothing frozen")
    if report["threshold"] != cv["selected_threshold"]:
        raise ValueError("the gate was judged at another threshold")
    params = build_model().get_params()
    config = {
        "rubric": PROMPT_VERSION,
        "preprocessing": cv["selected"],
        "classifier": "TF-IDF + LogisticRegression (baseline.build_model)",
        "classifier_params": {
            key: repr(value) for key, value in sorted(params.items()) if "__" in key
        },
        "threshold": cv["selected_threshold"],
        "gate": "eligibility_gate (conservative, v2 after the 1040 fix)",
        "gate_source_sha256": _sha256(Path(eligibility_gate.__file__)),
        "temporal_cv_sha256": _sha256(FROZEN),
        "gate_experiment_sha256": _sha256(experiment),
        "train_sha256": _sha256(SPLIT_DIR / "train.jsonl"),
        "validation_sha256": _sha256(SPLIT_DIR / "validation.jsonl"),
        "split_metadata_sha256": _sha256(SPLIT_DIR / "split_metadata.json"),
        "dev_labels_sha256": _sha256(DEV_DIR / LABELS),
        "validation_401_status": "development observation (gate v1 missed 1040)",
        "holdout_status": "not evaluated; its labels belong to the previous target",
        "created_at": created_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
    }
    write_atomically(out, json.dumps(config, ensure_ascii=False, indent=2) + "\n")
    return config


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args == ["freeze"]:
        try:
            config = freeze(datetime.now(UTC))
        except (FileExistsError, ValueError) as error:
            print(f"refused: {error}", file=sys.stderr)
            return 1
        print(json.dumps(config, ensure_ascii=False, indent=2))
        return 0
    if args:
        print(__doc__, file=sys.stderr)
        return 2
    frozen = json.loads(guarded(FROZEN).read_text(encoding="utf-8"))
    if frozen["selected"] != "lemma":
        print(f"refused: the frozen baseline is {frozen['selected']!r}", file=sys.stderr)
        return 1
    report = run(
        SPLIT_DIR / "train.jsonl", SPLIT_DIR / "validation.jsonl", frozen["selected_threshold"]
    )
    out = guarded(RESULTS_DIR / "gate_experiment.json")
    write_atomically(out, json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"gate_improves_cv": report["gate_improves_cv"]}))
    print(f"results: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
