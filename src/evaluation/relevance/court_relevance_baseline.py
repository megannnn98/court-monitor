"""The relevance baseline on the Court Monitor target: `relevant_to_court_monitor`.

    python -m evaluation.relevance.court_relevance_baseline

The TF-IDF + LogisticRegression of `baseline.build_model`, unchanged, fitted on the
frozen development train and judged on its validation, once per preprocessing of
`preprocessing.PREPROCESSORS`. The threshold of a variant is the most precise one with
validation recall ≥ 0.90; when no threshold reaches it, the report says so and picks
nothing. The variant is chosen the same way, then by PR-AUC, then by simplicity.

The frozen holdout of 400 is never read: every path this experiment opens is checked
against it first (`HoldoutAccessError`).
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from evaluation.relevance.ablation import tracked_weights
from evaluation.relevance.analysis import model_features, point_metrics
from evaluation.relevance.baseline import (
    Article,
    best_at_recall,
    build_model,
    calculate_metrics,
    probabilities_of,
    texts_of,
)
from evaluation.relevance.holdout import write_atomically
from evaluation.relevance.preprocessing import PREPROCESSORS

ROOT = Path(__file__).resolve().parents[3]
SPLIT_DIR = ROOT / "data/ml/relevance/court_monitor"
RESULTS_DIR = SPLIT_DIR / "results"
HOLDOUT_DIR = ROOT / "data/ml/relevance/holdout"
FIELD = "relevant_to_court_monitor"
TARGET_RECALL = 0.90
RECALL_POINTS = (0.80, 0.85, 0.90, 0.95)
DEFAULT_THRESHOLD = 0.5
ASSISTANT_ADJUDICATION = "assistant_adjudication"
TRACKED = (
    "овд",
    "инфо",
    "овд инфо",
    "sota",
    "sotavision",
    "подписывайтесь",
    "max",
    "https",
    "telegram",
    "baza",
    "медиазона",
    "суд",
    "уголовное",
    "уголовное дело",
    "ук",
    "приговор",
    "сентября",
    "августа",
    "2026",
    "2022",
)


class HoldoutAccessError(RuntimeError):
    """The experiment tried to read the frozen holdout."""


def guarded(path: Path, holdout_dir: Path = HOLDOUT_DIR) -> Path:
    """`path`, unless it lies in the frozen holdout."""
    resolved, holdout = path.resolve(), holdout_dir.resolve()
    if resolved == holdout or holdout in resolved.parents:
        raise HoldoutAccessError(f"{path} is in the frozen holdout")
    return path


def load_split(path: Path) -> list[Article]:
    """A split file as articles: the target as the label, the provenance as the kind."""
    rows = [
        json.loads(line)
        for line in guarded(path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not rows:
        raise ValueError(f"{path}: no articles")
    articles = []
    for row in rows:
        if not isinstance(row[FIELD], bool):
            raise TypeError(f"{path}: {row['article_id']} has no true/false {FIELD}")
        articles.append(
            Article(
                article_id=int(row["article_id"]),
                title=str(row["title"]),
                text=str(row["text"]),
                published_at=datetime.fromisoformat(row["published_at"]),
                label=int(row[FIELD]),
                kind=str(row["annotation_source"]),
                source=str(row["source"]),
                stratum="",
            )
        )
    return articles


def _metrics(
    labels: Sequence[int], probabilities: Sequence[float], threshold: float
) -> dict[str, Any]:
    metrics = calculate_metrics(labels, probabilities, threshold)
    return {key: value for key, value in metrics.items() if key != "confusion_matrix"}


def evaluate(
    train: Sequence[Article],
    validation: Sequence[Article],
    preprocess: Callable[[str], str],
    model_factory: Callable[[], Any] = build_model,
) -> dict[str, Any]:
    """One preprocessing: fitted on train, judged on validation."""
    model = model_factory()
    model.fit(texts_of(train, preprocess), [row.label for row in train])
    probabilities = probabilities_of(model, validation, preprocess)
    labels = [row.label for row in validation]
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
    return {
        "model": model,
        "probabilities": probabilities,
        "target_met": chosen is not None,
        "threshold": None if chosen is None else chosen.threshold,
        "at_target": None if chosen is None else _metrics(labels, probabilities, chosen.threshold),
        "at_0_5": _metrics(labels, probabilities, DEFAULT_THRESHOLD),
        "operating_points": points,
    }


def choose(results: Mapping[str, Mapping[str, Any]]) -> str | None:
    """The most precise variant at recall ≥ 0.90, then PR-AUC, then the simpler; None
    when no variant reaches the recall — never a fallback."""
    order = {name: index for index, name in enumerate(results)}
    reaching = [name for name, result in results.items() if result["target_met"]]
    if not reaching:
        return None
    return max(
        reaching,
        key=lambda name: (
            results[name]["at_target"]["precision"],
            results[name]["at_target"]["average_precision"],
            -order[name],
        ),
    )


def errors(
    rows: Sequence[Article], probabilities: Sequence[float], threshold: float, limit: int = 20
) -> dict[str, Any]:
    """The most confident false alarms and the misses, each with what identifies it."""

    def entry(row: Article, probability: float) -> dict[str, Any]:
        return {
            "article_id": row.article_id,
            "probability": probability,
            "margin": probability - threshold,
            "title": row.title,
            "source": row.source,
            "date": row.published_at.isoformat(),
            "annotation_source": row.kind,
            "snippet": " ".join(row.text.split())[:300],
        }

    pairs = list(zip(rows, probabilities, strict=True))
    fp = [entry(r, p) for r, p in pairs if not r.label and p >= threshold]
    fn = [entry(r, p) for r, p in pairs if r.label and p < threshold]
    fp.sort(key=lambda e: (-e["probability"], e["article_id"]))
    fn.sort(key=lambda e: (-e["probability"], e["article_id"]))
    return {
        "false_positives": fp[:limit],
        "false_negatives": fn[:limit],
        "false_positive_count": len(fp),
        "false_negative_count": len(fn),
    }


def sensitivity(
    rows: Sequence[Article], probabilities: Sequence[float], threshold: float
) -> dict[str, Any]:
    """The same threshold on all of validation and on validation without the
    assistant's adjudications: how much the metrics lean on them."""
    kept = [
        (r, p) for r, p in zip(rows, probabilities, strict=True) if r.kind != ASSISTANT_ADJUDICATION
    ]
    return {
        "assistant_adjudication_in_validation": len(rows) - len(kept),
        "full": {"total": len(rows), **_metrics([r.label for r in rows], probabilities, threshold)},
        "without_assistant_adjudication": {
            "total": len(kept),
            **_metrics([r.label for r, _ in kept], [p for _, p in kept], threshold),
        },
    }


def run(
    train_path: Path,
    validation_path: Path,
    preprocessors: Mapping[str, Callable[[str], str]] = PREPROCESSORS,
    model_factory: Callable[[], Any] = build_model,
) -> dict[str, Any]:
    train, validation = load_split(train_path), load_split(validation_path)
    results = {
        name: evaluate(train, validation, preprocess, model_factory)
        for name, preprocess in preprocessors.items()
    }
    selected = choose(results)
    report: dict[str, Any] = {
        "target": FIELD,
        "target_recall": TARGET_RECALL,
        "train": {"total": len(train), "relevant": sum(r.label for r in train)},
        "validation": {"total": len(validation), "relevant": sum(r.label for r in validation)},
        "variants": {
            name: {
                key: value for key, value in result.items() if key not in ("model", "probabilities")
            }
            | {"tracked_features": tracked_weights(result["model"], TRACKED)}
            for name, result in results.items()
        },
        "selected": selected,
        "selection_source": "validation",
        "holdout_read": False,
    }
    if selected is not None:
        best = results[selected]
        report["selected_threshold"] = best["threshold"]
        report["errors"] = errors(validation, best["probabilities"], best["threshold"])
        report["top_features"] = model_features(best["model"])
        report["sensitivity"] = sensitivity(validation, best["probabilities"], best["threshold"])
    return report


def main() -> int:
    report = run(SPLIT_DIR / "train.jsonl", SPLIT_DIR / "validation.jsonl")
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out = guarded(RESULTS_DIR / "baseline_ablation.json")
    write_atomically(out, json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({k: report[k] for k in ("selected", "selection_source")}, ensure_ascii=False))
    print(f"results: {out}")
    return 0 if report["selected"] is not None else 1


if __name__ == "__main__":
    sys.exit(main())
