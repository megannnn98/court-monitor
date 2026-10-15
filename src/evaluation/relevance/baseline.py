"""TF-IDF + LogisticRegression relevance baseline, evaluated without peeking at the test.

The labelled train file is split by time: its earliest 80% train the model, its latest 20%
choose the decision threshold. The test file — later than all of train — is scored once,
by that model at that threshold. Nothing of the test reaches fitting or the threshold.

The threshold serves Court Monitor's need to miss few cases: of the thresholds that keep
recall(case) at the target, the one with the highest precision. If no threshold reaches
the target, the one with the highest recall, and the report says the target was missed.

The model scored on the test is the one trained on the train part only: refitting on
train + validation would shift the probabilities the threshold was chosen for.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

TARGET_RECALL = 0.90
VALIDATION_SHARE = 0.2

_FIELDS = ("article_id", "title", "text", "published_at", "label", "kind", "source", "stratum")


@dataclass(frozen=True)
class Article:
    article_id: int
    title: str
    text: str
    published_at: datetime
    label: int
    kind: str
    source: str
    stratum: str


@dataclass(frozen=True)
class ThresholdChoice:
    threshold: float
    precision: float
    recall: float
    target_recall: float
    target_met: bool


@dataclass(frozen=True)
class Fitted:
    """What is decided before the test is looked at."""

    model: Any = field(compare=False)
    train: list[Article]
    validation: list[Article]
    validation_probabilities: list[float]
    threshold: ThresholdChoice


@dataclass(frozen=True)
class Report:
    sizes: dict[str, int]
    threshold: ThresholdChoice
    validation: dict[str, Any]
    test: dict[str, Any]
    false_negatives: list[dict[str, Any]]
    false_positives: list[dict[str, Any]]
    fitted: Fitted
    test_probabilities: list[float]


def _article(raw: object, where: str) -> Article:
    if not isinstance(raw, dict):
        raise TypeError(f"{where}: not a JSON object")
    missing = [field for field in _FIELDS if field not in raw]
    if missing:
        raise ValueError(f"{where}: missing {', '.join(missing)}")
    label = raw["label"]
    if type(label) is not int or label not in (0, 1):
        raise ValueError(f"{where}: label must be 0 or 1, got {label!r}")
    try:
        published_at = datetime.fromisoformat(raw["published_at"])
    except (TypeError, ValueError) as error:
        raise ValueError(f"{where}: bad published_at {raw['published_at']!r}") from error
    if published_at.tzinfo is None:
        raise ValueError(f"{where}: published_at without a time zone")
    return Article(
        article_id=int(raw["article_id"]),
        title=str(raw["title"]),
        text=str(raw["text"]),
        published_at=published_at,
        label=label,
        kind=str(raw["kind"]),
        source=str(raw["source"]),
        stratum=str(raw["stratum"]),
    )


def load_dataset(path: Path) -> list[Article]:
    """The articles of a JSONL file; a malformed line is an error naming its number."""
    rows: list[Article] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        where = f"{path.name}:{number}"
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"{where}: not JSON") from error
        rows.append(_article(raw, where))
    if not rows:
        raise ValueError(f"{path}: no articles")
    return rows


def _both_classes(rows: Sequence[Article], part: str) -> None:
    if len({row.label for row in rows}) < 2:
        raise ValueError(f"{part}: needs both classes")


def temporal_split(
    rows: Sequence[Article], validation_share: float = VALIDATION_SHARE
) -> tuple[list[Article], list[Article]]:
    """The earliest articles to train on, the latest to validate on. Ties of time are
    broken by article id, so the split never depends on the order of the input."""
    if not 0 < validation_share < 1:
        raise ValueError(f"validation share must be within (0, 1), got {validation_share}")
    ordered = sorted(rows, key=lambda row: (row.published_at, row.article_id))
    cut = round(len(ordered) * (1 - validation_share))
    train, validation = ordered[:cut], ordered[cut:]
    if not train or not validation:
        raise ValueError(f"{len(ordered)} articles are too few to split")
    _both_classes(train, "train")
    _both_classes(validation, "validation")
    return train, validation


def article_text(row: Article) -> str:
    # The title carries a strong signal, so it goes in with the text.
    return f"{row.title}\n\n{row.text}"


def build_model(random_state: int = 0) -> Any:
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import Pipeline

    return Pipeline(
        [
            (
                "tfidf",
                TfidfVectorizer(
                    lowercase=True,
                    ngram_range=(1, 2),
                    min_df=2,
                    max_df=0.95,
                    max_features=50_000,
                    sublinear_tf=True,
                ),
            ),
            (
                "classifier",
                LogisticRegression(
                    max_iter=2000,
                    class_weight="balanced",
                    random_state=random_state,
                ),
            ),
        ]
    )


def _checked(labels: Sequence[int], probabilities: Sequence[float]) -> None:
    if not labels:
        raise ValueError("no labels")
    if len(labels) != len(probabilities):
        raise ValueError(f"{len(labels)} labels but {len(probabilities)} probabilities")
    if not any(labels):
        raise ValueError("no positive labels: recall is undefined")


def confusion_counts(
    labels: Sequence[int], probabilities: Sequence[float], threshold: float
) -> tuple[int, int, int, int]:
    """TN, FP, FN, TP of predicting a case when the probability reaches the threshold."""
    tn = fp = fn = tp = 0
    for label, probability in zip(labels, probabilities, strict=True):
        predicted = probability >= threshold
        if label:
            tp += predicted
            fn += not predicted
        else:
            fp += predicted
            tn += not predicted
    return tn, fp, fn, tp


def select_threshold(
    labels: Sequence[int],
    probabilities: Sequence[float],
    target_recall: float = TARGET_RECALL,
) -> ThresholdChoice:
    """Of the model's own probabilities as thresholds: the highest precision among those
    with recall at the target, a higher threshold on a tie; if none reaches the target,
    the highest recall, then the highest precision, then the higher threshold."""
    best = best_at_recall(labels, probabilities, target_recall)
    if best is not None:
        return best
    threshold, precision, recall = max(
        _scored(labels, probabilities), key=lambda e: (e[2], e[1], e[0])
    )
    return ThresholdChoice(
        threshold=threshold,
        precision=precision,
        recall=recall,
        target_recall=target_recall,
        target_met=False,
    )


def _scored(
    labels: Sequence[int], probabilities: Sequence[float]
) -> list[tuple[float, float, float]]:
    """(threshold, precision, recall) at each of the model's own probabilities."""
    _checked(labels, probabilities)
    scored: list[tuple[float, float, float]] = []
    for threshold in sorted(set(probabilities)):
        _, fp, fn, tp = confusion_counts(labels, probabilities, threshold)
        scored.append((threshold, tp / (tp + fp), tp / (tp + fn)))
    return scored


def best_at_recall(
    labels: Sequence[int], probabilities: Sequence[float], target_recall: float
) -> ThresholdChoice | None:
    """The highest precision among thresholds with recall at the target, the higher
    threshold on a tie; None when no threshold reaches the target."""
    reaching = [e for e in _scored(labels, probabilities) if e[2] >= target_recall]
    if not reaching:
        return None
    threshold, precision, recall = max(reaching, key=lambda e: (e[1], e[0]))
    return ThresholdChoice(
        threshold=threshold,
        precision=precision,
        recall=recall,
        target_recall=target_recall,
        target_met=True,
    )


def calculate_metrics(
    labels: Sequence[int], probabilities: Sequence[float], threshold: float
) -> dict[str, Any]:
    from sklearn.metrics import (
        accuracy_score,
        average_precision_score,
        confusion_matrix,
        f1_score,
        precision_score,
        recall_score,
        roc_auc_score,
    )

    _checked(labels, probabilities)
    predicted = [int(probability >= threshold) for probability in probabilities]
    tn, fp, fn, tp = (
        int(value) for value in confusion_matrix(labels, predicted, labels=[0, 1]).ravel()
    )
    return {
        "threshold": threshold,
        "precision": float(precision_score(labels, predicted, zero_division=0)),
        "recall": float(recall_score(labels, predicted, zero_division=0)),
        "f1": float(f1_score(labels, predicted, zero_division=0)),
        "accuracy": float(accuracy_score(labels, predicted)),
        "roc_auc": float(roc_auc_score(labels, probabilities)),
        "average_precision": float(average_precision_score(labels, probabilities)),
        "confusion_matrix": [[tn, fp], [fn, tp]],
        "tn": tn,
        "fp": fp,
        "fn": fn,
        "tp": tp,
    }


def collect_errors(
    rows: Sequence[Article], probabilities: Sequence[float], threshold: float
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Missed cases and false alarms, each from the highest probability down: the
    borderline misses and the most confident false alarms first."""
    if len(rows) != len(probabilities):
        raise ValueError(f"{len(rows)} articles but {len(probabilities)} probabilities")
    false_negatives: list[dict[str, Any]] = []
    false_positives: list[dict[str, Any]] = []
    for row, probability in zip(rows, probabilities, strict=True):
        predicted = probability >= threshold
        if row.label == predicted:
            continue
        error = {
            "article_id": row.article_id,
            "title": row.title,
            "published_at": row.published_at.isoformat(),
            "kind": row.kind,
            "source": row.source,
            "probability": probability,
            "threshold": threshold,
            "margin": probability - threshold,
        }
        (false_negatives if row.label else false_positives).append(error)
    for errors in (false_negatives, false_positives):
        errors.sort(key=lambda error: (-error["probability"], error["article_id"]))
    return false_negatives, false_positives


def _unchanged(text: str) -> str:
    return text


def texts_of(rows: Sequence[Article], preprocess: Callable[[str], str] = _unchanged) -> list[str]:
    """What the model reads of each article: its text, after the preprocessing under test."""
    return [preprocess(article_text(row)) for row in rows]


def probabilities_of(
    model: Any, rows: Sequence[Article], preprocess: Callable[[str], str] = _unchanged
) -> list[float]:
    return [float(p) for p in model.predict_proba(texts_of(rows, preprocess))[:, 1]]


def fit_and_choose_threshold(
    rows: Sequence[Article],
    *,
    target_recall: float = TARGET_RECALL,
    model_factory: Callable[[], Any] = build_model,
    preprocess: Callable[[str], str] = _unchanged,
) -> Fitted:
    """Everything decided before the test: the model fitted on the earlier part, the
    threshold chosen on the later one. It is given the train file only."""
    train, validation = temporal_split(rows)
    model = model_factory()
    model.fit(texts_of(train, preprocess), [row.label for row in train])
    probabilities = probabilities_of(model, validation, preprocess)
    choice = select_threshold([row.label for row in validation], probabilities, target_recall)
    return Fitted(
        model=model,
        train=train,
        validation=validation,
        validation_probabilities=probabilities,
        threshold=choice,
    )


def run(
    train_rows: Sequence[Article],
    test_rows: Sequence[Article],
    *,
    target_recall: float = TARGET_RECALL,
    model_factory: Callable[[], Any] = build_model,
    preprocess: Callable[[str], str] = _unchanged,
) -> Report:
    fitted = fit_and_choose_threshold(
        train_rows,
        target_recall=target_recall,
        model_factory=model_factory,
        preprocess=preprocess,
    )
    if not test_rows:
        raise ValueError("no test articles")
    threshold = fitted.threshold.threshold
    # The one and only look at the test.
    probabilities = probabilities_of(fitted.model, test_rows, preprocess)
    labels = [row.label for row in test_rows]
    false_negatives, false_positives = collect_errors(test_rows, probabilities, threshold)
    return Report(
        sizes={
            "train": len(fitted.train),
            "validation": len(fitted.validation),
            "test": len(test_rows),
        },
        threshold=fitted.threshold,
        validation=calculate_metrics(
            [row.label for row in fitted.validation],
            fitted.validation_probabilities,
            threshold,
        ),
        test=calculate_metrics(labels, probabilities, threshold),
        false_negatives=false_negatives,
        false_positives=false_positives,
        fitted=fitted,
        test_probabilities=probabilities,
    )


def _write_jsonl(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8"
    )


def write_results(report: Report, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    metrics = {
        "sizes": report.sizes,
        "threshold": asdict(report.threshold),
        "validation": report.validation,
        "test": report.test,
        "errors": {
            "false_negatives": len(report.false_negatives),
            "false_positives": len(report.false_positives),
        },
    }
    (out_dir / "metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    _write_jsonl(out_dir / "false_negatives.jsonl", report.false_negatives)
    _write_jsonl(out_dir / "false_positives.jsonl", report.false_positives)
