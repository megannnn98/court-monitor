"""Train the TF-IDF + LogisticRegression relevance baseline, evaluate it once on the
untouched test and read its errors. The logic lives in `evaluation.relevance`.

    PYTHONPATH=src python scripts/train_relevance_baseline.py
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from evaluation.relevance import analysis
from evaluation.relevance.baseline import Report, load_dataset, run, write_results

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data/ml/relevance"
RESULTS_DIR = DATA_DIR / "results"


def _print_metrics(name: str, metrics: dict[str, object]) -> None:
    print(f"{name}:")
    for key in ("precision", "recall", "f1", "accuracy", "roc_auc", "average_precision"):
        print(f"  {key:<18} {metrics[key]:.3f}")
    print(f"  confusion matrix   {metrics['confusion_matrix']}")
    print(f"  TN={metrics['tn']} FP={metrics['fp']} FN={metrics['fn']} TP={metrics['tp']}")


def _print_report(report: Report) -> None:
    print(f"sizes: {report.sizes}")
    choice = report.threshold
    print(
        f"threshold: {choice.threshold:.4f} "
        f"(validation precision {choice.precision:.3f}, recall {choice.recall:.3f}; "
        f"target recall {choice.target_recall:.2f} "
        f"{'met' if choice.target_met else 'NOT met — highest recall taken'})"
    )
    _print_metrics("validation", report.validation)
    _print_metrics("test", report.test)
    print(f"errors on test: {len(report.false_negatives)} FN, {len(report.false_positives)} FP")


def _row(point: dict[str, Any]) -> str:
    return (
        f"{point['precision_case']:>6.3f} {point['recall_case']:>6.3f} {point['f1_case']:>6.3f} "
        f"{point['accuracy']:>6.3f} {point['tn']:>4} {point['fp']:>4} {point['fn']:>4} "
        f"{point['tp']:>4}"
    )


def _print_curve(validation: Sequence[dict[str, Any]], test: Sequence[dict[str, Any]]) -> None:
    head = "  prec    rec     f1    acc   TN   FP   FN   TP"
    print(f"\nthreshold curve\n{'thr':>7} | {'validation':<46}| test\n{'':>7} |{head} |{head}")
    for v, t in zip(validation, test, strict=True):
        print(f"{v['threshold']:>7.4f} | {_row(v)} | {_row(t)}")


def _print_points(points: Sequence[dict[str, Any]]) -> None:
    print("\noperating points (chosen on validation; test only shows how they hold)")
    for point in points:
        if point["status"] == "unreachable":
            print(f"  recall ≥ {point['target_recall']:.2f}: unreachable on validation")
            continue
        v, t = point["validation"], point["test"]
        print(
            f"  recall ≥ {point['target_recall']:.2f}: thr {point['threshold']:.4f} | "
            f"val P {v['precision_case']:.3f} R {v['recall_case']:.3f} FP {v['fp']} FN {v['fn']}"
            f" | test P {t['precision_case']:.3f} R {t['recall_case']:.3f}"
            f" FP {t['fp']} FN {t['fn']}"
        )


def _print_errors(
    fp_kinds: Sequence[dict[str, Any]],
    fn: Sequence[dict[str, Any]],
    fn_counts: dict[str, dict[str, int]],
    near: dict[str, list[dict[str, Any]]],
) -> None:
    print("\nFP by kind:  count  share   mean  median")
    for entry in fp_kinds:
        print(
            f"  {entry['kind']:<15} {entry['count']:>4} {entry['share']:>6.1%} "
            f"{entry['mean_probability']:>6.3f} {entry['median_probability']:>6.3f}"
        )
    print("\nFN:")
    for entry in fn:
        print(
            f"  {entry['article_id']:>6} p={entry['probability']:.3f} "
            f"m={entry['margin']:+.3f} len={entry['text_length']:>5} "
            f"legal={entry['legal_words']!s:<5} {entry['stratum']:<13} {entry['source']:<22} "
            f"{entry['title'][:60]}"
        )
    for field, counts in fn_counts.items():
        print(f"  FN by {field}: {counts}")
    print("\nnear the threshold (top 5 of each outcome):")
    for outcome in ("tp", "fn", "fp", "tn"):
        for entry in near[outcome][:5]:
            print(
                f"  {outcome.upper()} {entry['article_id']:>6} p={entry['probability']:.3f} "
                f"m={entry['margin']:+.4f} {entry['kind']:<15} {entry['title'][:60]}"
            )


def _print_features(features: dict[str, list[dict[str, Any]]]) -> None:
    for side in ("case", "not_case"):
        print(f"\ntop features → {side}:")
        print(
            "  " + ", ".join(f"{pair['feature']} {pair['weight']:+.2f}" for pair in features[side])
        )


def _write_json(name: str, value: object) -> None:
    (RESULTS_DIR / name).write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def main() -> None:
    train_rows = load_dataset(DATA_DIR / "train.jsonl")
    test_rows = load_dataset(DATA_DIR / "test.jsonl")
    report = run(train_rows, test_rows)
    fitted = report.fitted
    threshold = fitted.threshold.threshold
    validation_labels = [row.label for row in fitted.validation]
    test_labels = [row.label for row in test_rows]

    thresholds = analysis.curve_thresholds(threshold)
    curve_validation = analysis.threshold_curve(
        validation_labels, fitted.validation_probabilities, thresholds
    )
    curve_test = analysis.threshold_curve(test_labels, report.test_probabilities, thresholds)
    points = analysis.operating_points(
        validation_labels,
        fitted.validation_probabilities,
        test_labels,
        report.test_probabilities,
    )
    fp_kinds = analysis.fp_by_kind(report.false_positives)
    fn = analysis.fn_details(test_rows, report.test_probabilities, threshold)
    fn_counts = analysis.fn_summary(fn)
    near = analysis.near_threshold(test_rows, report.test_probabilities, threshold)
    features = analysis.model_features(fitted.model)
    leakage = analysis.duplicates(
        {"train": fitted.train, "validation": fitted.validation, "test": test_rows}
    )

    _print_report(report)
    _print_curve(curve_validation, curve_test)
    _print_points(points)
    _print_errors(fp_kinds, fn, fn_counts, near)
    _print_features(features)
    print(f"\nduplicates across splits: {leakage['counts']}")

    write_results(report, RESULTS_DIR)
    _write_json(
        "threshold_curve.json",
        {
            "chosen_threshold": threshold,
            "validation": curve_validation,
            "test": curve_test,
            "operating_points": points,
        },
    )
    _write_json(
        "error_summary.json",
        {
            "threshold": threshold,
            "fp_by_kind": fp_kinds,
            "false_negatives": fn,
            "fn_summary": fn_counts,
            "near_threshold": near,
        },
    )
    _write_json("top_features.json", features)
    _write_json("leakage_report.json", leakage)
    print(f"\nresults: {RESULTS_DIR}")


if __name__ == "__main__":
    main()
