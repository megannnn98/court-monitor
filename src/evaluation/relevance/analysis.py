"""Reading the baseline: what each threshold costs, where it errs, which words it leans on,
and which articles the splits share.

Operating points are chosen on validation only; the test is given each chosen threshold
afterwards, to show how it holds — never to pick one. Nothing here changes the data or the
model: duplicates are reported, not removed.
"""

from __future__ import annotations

import math
import statistics
from collections import Counter
from collections.abc import Mapping, Sequence
from itertools import combinations
from typing import Any

from evaluation.junk_screen.corpus import LEGAL_WORDS
from evaluation.relevance.baseline import Article, best_at_recall, confusion_counts

CURVE_THRESHOLDS = (0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50, 0.60, 0.70)
RECALL_TARGETS = (0.80, 0.85, 0.90, 0.95)
NEAR_THRESHOLD = 20
TOP_FEATURES = 30


def point_metrics(
    labels: Sequence[int], probabilities: Sequence[float], threshold: float
) -> dict[str, Any]:
    """Case-class metrics of one threshold; a ratio with nothing to divide is 0."""
    tn, fp, fn, tp = confusion_counts(labels, probabilities, threshold)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "threshold": threshold,
        "precision_case": precision,
        "recall_case": recall,
        "f1_case": f1,
        "accuracy": (tp + tn) / len(labels) if labels else 0.0,
        "tn": tn,
        "fp": fp,
        "fn": fn,
        "tp": tp,
    }


def curve_thresholds(chosen: float, fixed: Sequence[float] = CURVE_THRESHOLDS) -> list[float]:
    """The fixed thresholds and the chosen one, ascending; the chosen one is not repeated
    when it equals a fixed one."""
    thresholds = list(fixed)
    if not any(math.isclose(chosen, value, abs_tol=1e-12) for value in fixed):
        thresholds.append(chosen)
    return sorted(thresholds)


def threshold_curve(
    labels: Sequence[int], probabilities: Sequence[float], thresholds: Sequence[float]
) -> list[dict[str, Any]]:
    if len(labels) != len(probabilities):
        raise ValueError(f"{len(labels)} labels but {len(probabilities)} probabilities")
    return [point_metrics(labels, probabilities, threshold) for threshold in thresholds]


def operating_points(
    validation_labels: Sequence[int],
    validation_probabilities: Sequence[float],
    test_labels: Sequence[int],
    test_probabilities: Sequence[float],
    targets: Sequence[float] = RECALL_TARGETS,
) -> list[dict[str, Any]]:
    """For each recall target, the threshold with the highest validation precision at
    validation recall ≥ target (the higher threshold on a tie), and how the test fares at
    it. A target no validation threshold reaches is `unreachable` and has no threshold."""
    points: list[dict[str, Any]] = []
    for target in targets:
        best = best_at_recall(validation_labels, validation_probabilities, target)
        if best is None:
            points.append({"target_recall": target, "status": "unreachable"})
            continue
        points.append(
            {
                "target_recall": target,
                "status": "reachable",
                "threshold": best.threshold,
                "validation": point_metrics(
                    validation_labels, validation_probabilities, best.threshold
                ),
                "test": point_metrics(test_labels, test_probabilities, best.threshold),
            }
        )
    return points


def fp_by_kind(false_positives: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """False alarms per kind: how many, their share, mean and median probability; the
    most frequent kind first."""
    by_kind: dict[str, list[float]] = {}
    for error in false_positives:
        by_kind.setdefault(error["kind"], []).append(error["probability"])
    total = len(false_positives)
    summary = [
        {
            "kind": kind,
            "count": len(values),
            "share": len(values) / total,
            "mean_probability": statistics.fmean(values),
            "median_probability": statistics.median(values),
        }
        for kind, values in by_kind.items()
    ]
    return sorted(summary, key=lambda entry: (-entry["count"], entry["kind"]))


def fn_details(
    rows: Sequence[Article], probabilities: Sequence[float], threshold: float
) -> list[dict[str, Any]]:
    """Each missed case with what might explain the miss, the closest miss first."""
    details = [
        {
            "article_id": row.article_id,
            "title": row.title,
            "source": row.source,
            "kind": row.kind,
            "stratum": row.stratum,
            "probability": probability,
            "margin": probability - threshold,
            "text_length": len(row.text),
            "legal_words": bool(LEGAL_WORDS.search(f"{row.title}\n{row.text}")),
        }
        for row, probability in zip(rows, probabilities, strict=True)
        if row.label == 1 and probability < threshold
    ]
    return sorted(details, key=lambda entry: (-entry["probability"], entry["article_id"]))


def fn_summary(details: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, int]]:
    """How the missed cases spread over source, stratum and legal words."""

    def counted(field: str) -> dict[str, int]:
        counts = Counter(str(entry[field]) for entry in details)
        return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))

    return {field: counted(field) for field in ("source", "stratum", "legal_words")}


def near_threshold(
    rows: Sequence[Article],
    probabilities: Sequence[float],
    threshold: float,
    limit: int = NEAR_THRESHOLD,
) -> dict[str, list[dict[str, Any]]]:
    """The articles closest to the threshold, all together and per outcome (TP, FN, FP,
    TN): the closest first, ties by article id. No texts, only what identifies them."""
    entries: list[tuple[str, dict[str, Any]]] = []
    for row, probability in zip(rows, probabilities, strict=True):
        predicted = probability >= threshold
        outcome = ("tp" if predicted else "fn") if row.label else ("fp" if predicted else "tn")
        entries.append(
            (
                outcome,
                {
                    "article_id": row.article_id,
                    "title": row.title,
                    "kind": row.kind,
                    "outcome": outcome,
                    "probability": probability,
                    "margin": probability - threshold,
                },
            )
        )
    entries.sort(key=lambda entry: (abs(entry[1]["margin"]), entry[1]["article_id"]))
    result = {"all": [entry for _, entry in entries[:limit]]}
    for outcome in ("tp", "fn", "fp", "tn"):
        result[outcome] = [entry for kind, entry in entries if kind == outcome][:limit]
    return result


def top_features(
    names: Sequence[str], weights: Sequence[float], limit: int = TOP_FEATURES
) -> dict[str, list[dict[str, Any]]]:
    """The words pulling hardest toward `case` (largest weights) and toward `not_case`
    (most negative); ties by the feature itself."""
    if len(names) != len(weights):
        raise ValueError(f"{len(names)} features but {len(weights)} weights")
    pairs = [(float(weight), name) for name, weight in zip(names, weights, strict=True)]
    case = sorted(pairs, key=lambda pair: (-pair[0], pair[1]))[:limit]
    not_case = sorted(pairs)[:limit]
    return {
        "case": [{"feature": name, "weight": weight} for weight, name in case if weight > 0],
        "not_case": [
            {"feature": name, "weight": weight} for weight, name in not_case if weight < 0
        ],
    }


def model_features(model: Any, limit: int = TOP_FEATURES) -> dict[str, list[dict[str, Any]]]:
    """`top_features` of the fitted TF-IDF + LogisticRegression pipeline."""
    names = model.named_steps["tfidf"].get_feature_names_out()
    weights = model.named_steps["classifier"].coef_[0]
    return top_features([str(name) for name in names], [float(w) for w in weights], limit)


def duplicates(splits: Mapping[str, Sequence[Article]]) -> dict[str, Any]:
    """Articles of different splits with exactly the same text, or the same title: both
    sides with their article id, split and label. Nothing is removed."""

    def pairs(key: str) -> list[dict[str, Any]]:
        found: dict[str, list[tuple[str, Article]]] = {}
        for split, rows in splits.items():
            for row in rows:
                value = getattr(row, key)
                if value.strip():
                    found.setdefault(value, []).append((split, row))
        result: list[dict[str, Any]] = []
        for value, holders in found.items():
            for (split_a, a), (split_b, b) in combinations(holders, 2):
                if split_a == split_b:
                    continue
                result.append(
                    {
                        key: value[:200],
                        "a": {"article_id": a.article_id, "split": split_a, "label": a.label},
                        "b": {"article_id": b.article_id, "split": split_b, "label": b.label},
                    }
                )
        return sorted(result, key=lambda pair: (pair["a"]["article_id"], pair["b"]["article_id"]))

    texts = pairs("text")
    titles = pairs("title")
    return {
        "exact_duplicate_texts": texts,
        "exact_duplicate_titles": titles,
        "counts": {
            "texts": _per_split_pair(texts),
            "titles": _per_split_pair(titles),
        },
    }


def _per_split_pair(pairs: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    counts = Counter("/".join(sorted((pair["a"]["split"], pair["b"]["split"]))) for pair in pairs)
    return dict(sorted(counts.items()))
