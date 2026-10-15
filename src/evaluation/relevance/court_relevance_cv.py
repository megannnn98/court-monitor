"""Temporal cross-validation of the relevance baseline inside the development train, then
one outer evaluation.

    python -m evaluation.relevance.court_relevance_cv

The train of `court_monitor_v2/` is ordered by publication time and cut into equal blocks;
fold k learns on the blocks before block k and is judged on block k (an expanding window),
so a fold's train is always earlier than its validation. An article whose normalized text
also stands in the fold's validation is left out of that fold's train, so no duplicate
group crosses a boundary. A validation fold with fewer than `MIN_FOLD_RELEVANT` relevant
articles stops everything — no other scheme is tried in its place.

Each preprocessing of `preprocessing.PREPROCESSORS` with the unchanged TF-IDF +
LogisticRegression of `baseline.build_model` gives out-of-fold probabilities on every
validation fold; on these pooled predictions alone the threshold (the most precise one
with recall ≥ 0.90) and the variant (`court_relevance_baseline.choose`) are chosen. Only
then is the chosen pipeline fitted on the whole train and applied, once and with that
threshold, to the outer validation — which no decision before has seen.

The frozen holdout is never read.
"""

from __future__ import annotations

import json
import math
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from evaluation.relevance.analysis import model_features, point_metrics
from evaluation.relevance.baseline import (
    Article,
    best_at_recall,
    build_model,
    calculate_metrics,
    probabilities_of,
    texts_of,
)
from evaluation.relevance.court_relevance_baseline import (
    RECALL_POINTS,
    TARGET_RECALL,
    choose,
    errors,
    guarded,
    load_split,
)
from evaluation.relevance.holdout import write_atomically
from evaluation.relevance.preprocessing import PREPROCESSORS

ROOT = Path(__file__).resolve().parents[3]
SPLIT_DIR = ROOT / "data/ml/relevance/court_monitor_v2"
RESULTS_DIR = SPLIT_DIR / "results"
N_FOLDS = 3
MIN_FOLD_RELEVANT = 20


class TooFewRelevantError(ValueError):
    """A validation fold holds too few relevant articles to judge a threshold on."""


def text_hashes(path: Path) -> dict[int, str]:
    """Each article's normalized-text hash, as the split file holds it."""
    rows = [json.loads(line) for line in guarded(path).read_text(encoding="utf-8").splitlines()]
    return {int(row["article_id"]): str(row["text_hash"]) for row in rows if row}


def temporal_folds(
    rows: Sequence[Article], hashes: Mapping[int, str], n_folds: int = N_FOLDS
) -> list[tuple[list[Article], list[Article], dict[str, Any]]]:
    """Expanding-window folds: (train, validation, what was decided) per fold."""
    ordered = sorted(rows, key=lambda r: (r.published_at, r.article_id))
    blocks = n_folds + 1
    if len(ordered) < blocks:
        raise ValueError("too few articles for the folds")
    edges = [ordered[math.ceil(len(ordered) * k / blocks)].published_at for k in range(1, blocks)]
    folds = []
    for k in range(n_folds):
        start = edges[k]
        end = edges[k + 1] if k + 1 < n_folds else None
        validation = [
            r for r in ordered if r.published_at >= start and (end is None or r.published_at < end)
        ]
        held = {hashes[r.article_id] for r in validation}
        before = [r for r in ordered if r.published_at < start]
        train = [r for r in before if hashes[r.article_id] not in held]
        info: dict[str, Any] = {
            "fold": k + 1,
            "train": {"total": len(train), "relevant": sum(r.label for r in train)},
            "validation": {"total": len(validation), "relevant": sum(r.label for r in validation)},
            "train_last": max(r.published_at for r in train).isoformat(),
            "validation_first": min(r.published_at for r in validation).isoformat(),
            "validation_last": max(r.published_at for r in validation).isoformat(),
            "left_out_of_train_as_duplicates": sorted(
                r.article_id for r in before if hashes[r.article_id] in held
            ),
        }
        folds.append((train, validation, info))
    short = [f[2] for f in folds if f[2]["validation"]["relevant"] < MIN_FOLD_RELEVANT]
    if short:
        raise TooFewRelevantError(json.dumps([f[2] for f in folds], ensure_ascii=False))
    return folds


def _summary(labels: Sequence[int], probabilities: Sequence[float]) -> dict[str, Any]:
    chosen = best_at_recall(labels, probabilities, TARGET_RECALL)
    points = []
    for target in RECALL_POINTS:
        best = best_at_recall(labels, probabilities, target)
        points.append(
            {"target_recall": target, "status": "unreachable"}
            if best is None
            else {
                "target_recall": target,
                "status": "reachable",
                **point_metrics(labels, probabilities, best.threshold),
            }
        )
    at_target = None
    if chosen is not None:
        metrics = calculate_metrics(labels, probabilities, chosen.threshold)
        at_target = {k: v for k, v in metrics.items() if k != "confusion_matrix"}
    return {
        "target_met": chosen is not None,
        "threshold": None if chosen is None else chosen.threshold,
        "at_target": at_target,
        "operating_points": points,
    }


def cross_validate(
    folds: Sequence[tuple[Sequence[Article], Sequence[Article], Mapping[str, Any]]],
    preprocessors: Mapping[str, Callable[[str], str]] = PREPROCESSORS,
    model_factory: Callable[[], Any] = build_model,
) -> dict[str, Any]:
    """Per variant: each fold's metrics and the pooled out-of-fold ones."""
    report: dict[str, Any] = {}
    for name, preprocess in preprocessors.items():
        pooled_labels: list[int] = []
        pooled: list[float] = []
        per_fold = []
        for train, validation, info in folds:
            model = model_factory()
            model.fit(texts_of(train, preprocess), [r.label for r in train])
            probabilities = probabilities_of(model, validation, preprocess)
            labels = [r.label for r in validation]
            pooled_labels += labels
            pooled += probabilities
            per_fold.append({"fold": info["fold"], **_summary(labels, probabilities)})
        report[name] = {"folds": per_fold, "pooled": _summary(pooled_labels, pooled)}
    return report


def outer(
    train: Sequence[Article],
    validation: Sequence[Article],
    preprocess: Callable[[str], str],
    threshold: float,
    model_factory: Callable[[], Any] = build_model,
) -> dict[str, Any]:
    """The chosen pipeline fitted on all of train, judged once on validation at the fixed
    threshold."""
    model = model_factory()
    model.fit(texts_of(train, preprocess), [r.label for r in train])
    probabilities = probabilities_of(model, validation, preprocess)
    labels = [r.label for r in validation]
    metrics = calculate_metrics(labels, probabilities, threshold)
    found = errors(validation, probabilities, threshold, limit=20)
    every_fn = errors(validation, probabilities, threshold, limit=len(validation))
    return {
        "threshold": threshold,
        "metrics": {k: v for k, v in metrics.items() if k != "confusion_matrix"},
        "false_positives_top20": found["false_positives"],
        "false_negatives": every_fn["false_negatives"],
        "false_positive_count": found["false_positive_count"],
        "false_negative_count": found["false_negative_count"],
        "top_features": model_features(model),
    }


def run(
    train_path: Path,
    validation_path: Path,
    preprocessors: Mapping[str, Callable[[str], str]] = PREPROCESSORS,
    model_factory: Callable[[], Any] = build_model,
) -> dict[str, Any]:
    train = load_split(train_path)
    folds = temporal_folds(train, text_hashes(train_path))
    cv = cross_validate(folds, preprocessors, model_factory)
    selected = choose({name: result["pooled"] for name, result in cv.items()})
    report: dict[str, Any] = {
        "folds": [info for _, _, info in folds],
        "cv": cv,
        "selected": selected,
        "selection_source": "pooled temporal CV inside train",
        "holdout_read": False,
    }
    if selected is None:
        return report
    threshold = cv[selected]["pooled"]["threshold"]
    report["selected_threshold"] = threshold
    # The outer validation is read only here, after every decision is made.
    validation = load_split(validation_path)
    report["outer_validation"] = outer(
        train, validation, preprocessors[selected], threshold, model_factory
    )
    return report


def main() -> int:
    try:
        report = run(SPLIT_DIR / "train.jsonl", SPLIT_DIR / "validation.jsonl")
    except TooFewRelevantError as error:
        print(f"stopped: a validation fold has fewer than {MIN_FOLD_RELEVANT} relevant: {error}")
        return 1
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out = guarded(RESULTS_DIR / "temporal_cv.json")
    write_atomically(out, json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({k: report.get(k) for k in ("selected", "selected_threshold")}))
    print(f"results: {out}")
    return 0 if report["selected"] is not None else 1


if __name__ == "__main__":
    sys.exit(main())
